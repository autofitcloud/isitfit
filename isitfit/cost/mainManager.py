import boto3
import pandas as pd
from tqdm import tqdm
import datetime as dt
import numpy as np
import pytz

import logging
logger = logging.getLogger('isitfit')


from ..utils import mergeSeriesOnTimestampRange, ec2_catalog, SECONDS_IN_ONE_DAY, NoCloudwatchException, myreturn
from .cloudtrail_ec2type import CloudtrailCached
from isitfit.cost.redshift.cloudwatchman import CloudwatchEc2

MINUTES_IN_ONE_DAY = 60*24 # 1440
N_DAYS=90

class NoCloudtrailException(Exception):
    pass


def tagsContain(f_tn, ec2_obj):
  if ec2_obj.tags is None:
    return False

  for t in ec2_obj.tags:
    for k in ['Key', 'Value']:
      if f_tn in t[k].lower():
        return True

  return False


from isitfit.cost.cacheManager import RedisPandas as RedisPandasCacheManager
class MainManager:
    def __init__(self, ctx, ddg=None, filter_tags=None, cache_man=None):
        # set start/end dates
        dt_now_d=dt.datetime.now().replace(tzinfo=pytz.utc)
        self.StartTime=dt_now_d - dt.timedelta(days=N_DAYS)
        self.EndTime=dt_now_d
        logger.debug("Metrics start..end: %s .. %s"%(self.StartTime, self.EndTime))

        # manager of redis-pandas caching
        self.cache_man = cache_man

        # boto3 cloudtrail data
        self.cloudtrail_manager = CloudtrailCached(dt_now_d, self.cache_man)

        # listeners post ec2 data fetch and post all activities
        self.listeners = {'pre':[], 'ec2': [], 'all': []}

        # datadog manager
        self.ddg = ddg

        # filtering by tags
        self.filter_tags = filter_tags

        # click context for errors
        self.ctx = ctx

        # generic iterator (iterates over regions and items)
        from isitfit.cost.redshift.iterator import Ec2Iterator
        self.ec2_it = Ec2Iterator()

        # cloudwatch manager
        self.cloudwatchman = CloudwatchEc2(self.cache_man)


    def add_listener(self, event, listener):
      if event not in self.listeners:
        from ..utils import IsitfitCliError
        raise IsitfitCliError("Internal dev error: Event %s is not supported for listeners. Use: %s"%(event, ",".join(self.listeners.keys())), self.ctx)

      self.listeners[event].append(listener)


    def ec2_count(self):
      # method 1
      # ec2_it = self.ec2_resource.instances.all()
      # return len(list(ec2_it))

      # method 2, using the generic iterator
      nc = len(list(self.ec2_it.iterate_core(True, True)))
      msg_count = "Found a total of %i EC2 instance(s) in %i region(s) (other regions do not hold any EC2)"
      logger.warning(msg_count%(nc, len(self.ec2_it.region_include)))
      return nc


    def ec2_iterator(self):
      # method 1
      # ec2_it = self.ec2_resource.instances.all()
      # return ec2_it

      # boto3 ec2 and cloudwatch data
      ec2_resource_all = {}

      # TODO cannot use directly use the iterator exposed in "ec2_it"
      # because it would return the dataframes from Cloudwatch,
      # whereas in the cloudwatch data fetch here, the data gets cached to redis.
      # Once the redshift.iterator can cache to redis, then the cloudwatch part here
      # can also be dropped, as well as using the "ec2_it" iterator directly
      # for ec2_dict in self.ec2_it:
      for ec2_dict in self.ec2_it.iterate_core(True, False):
        if ec2_dict['Region'] not in ec2_resource_all.keys():
          boto3.setup_default_session(region_name = ec2_dict['Region'])
          ec2_resource_all[ec2_dict['Region']] = boto3.resource('ec2')

        ec2_resource_single = ec2_resource_all[ec2_dict['Region']]
        ec2_l = ec2_resource_single.instances.filter(InstanceIds=[ec2_dict['InstanceId']])
        ec2_l = list(ec2_l)
        if len(ec2_l)==0:
          continue # not found

        # yield first entry
        ec2_obj = ec2_l[0]
        ec2_obj.region_name = ec2_dict['Region']
        yield ec2_obj


    def get_ifi(self):
        # set up caching if requested
        self.cache_man.fetch_envvars()
        if self.cache_man.isSetup():
          self.cache_man.connect()

        # 0th pass to count
        n_ec2_total = self.ec2_count()

        if n_ec2_total==0:
          return

        # if more than 10 servers, recommend caching with redis
        if n_ec2_total > 10 and not self.cache_man.isSetup():
            from termcolor import colored
            logger.warning(colored(
"""Since the number of EC2 instances is %i,
it is recommended to use redis for caching of downloaded CPU/memory metrics.
To do so
- install redis

    [sudo] apt-get install redis-server

- export environment variables

    export ISITFIT_REDIS_HOST=localhost
    export ISITFIT_REDIS_PORT=6379
    export ISITFIT_REDIS_DB=0

where ISITFIT_REDIS_DB is the ID of an unused database in redis.

And finally re-run isitfit as usual.
"""%n_ec2_total, "yellow"))
            continue_wo_redis = input(colored('Would you like to continue without redis caching (not recommended)? yes/[no] ', 'cyan'))
            if not (continue_wo_redis.lower() == 'yes' or continue_wo_redis.lower() == 'y'):
                logger.warning("Aborting.")
                return


        # call listeners
        for l in self.listeners['pre']:
          l()

        # download ec2 catalog: 2 columns: ec2 type, ec2 cost per hour
        self.df_cat = ec2_catalog()

        # get cloudtail ec2 type changes for all instances
        self.cloudtrail_manager.init_data(self.ec2_iterator(), self.ec2_it.region_include, n_ec2_total)

        # iterate over all ec2 instances
        n_ec2_analysed = 0
        sum_capacity = 0
        sum_used = 0
        df_all = []
        ec2_noCloudwatch = []
        ec2_noCloudtrail = []
        # Edit 2019-11-12 use "initial=0" instead of "=1". Check more details in a similar note in "cloudtrail_ec2type.py"
        for ec2_obj in tqdm(self.ec2_iterator(), total=n_ec2_total, desc="Pass 2/2 through EC2 instances", initial=0):
          # if filters requested, check that this instance passes
          if self.filter_tags is not None:
            f_tn = self.filter_tags.lower()
            passesFilter = tagsContain(f_tn, ec2_obj)
            if not passesFilter:
              continue

          try:
            ec2_df, ddg_df = self._handle_ec2obj(ec2_obj)
            n_ec2_analysed += 1

            # call listeners
            for l in self.listeners['ec2']:
              l(ec2_obj, ec2_df, self, ddg_df)

          except NoCloudwatchException:
            ec2_noCloudwatch.append(ec2_obj.instance_id)
          except NoCloudtrailException:
            ec2_noCloudtrail.append(ec2_obj.instance_id)

        # call listeners
        logger.info("... done")
        logger.info("")
        logger.info("")

        # get now + 10 minutes
        # http://stackoverflow.com/questions/6205442/ddg#6205529
        dt_now = dt.datetime.now()
        TRY_IN = 10
        now_plus_10 = dt_now + dt.timedelta(minutes = TRY_IN)
        now_plus_10 = now_plus_10.strftime("%H:%M")

        if len(ec2_noCloudwatch)>0:
          n_no_cw = len(ec2_noCloudwatch)
          has_more_cw = "..." if n_no_cw>5 else ""
          l_no_cw = ", ".join(ec2_noCloudwatch[:5])
          logger.warning("No cloudwatch data for %i instances: %s%s"%(n_no_cw, l_no_cw, has_more_cw))
          logger.warning("Try again in %i minutes (at %s) to check for new data"%(TRY_IN, now_plus_10))
          logger.info("")

        if len(ec2_noCloudtrail)>0:
          n_no_ct = len(ec2_noCloudtrail)
          has_more_ct = "..." if n_no_ct>5 else ""
          l_no_ct = ", ".join(ec2_noCloudtrail[:5])
          logger.warning("No cloudtrail data for %i instances: %s%s"%(n_no_ct, l_no_ct, has_more_ct))
          logger.warning("Try again in %i minutes (at %s) to check for new data"%(TRY_IN, now_plus_10))
          logger.info("")

        for l in self.listeners['all']:
          l(n_ec2_total, self, n_ec2_analysed, self.ec2_it.region_include)

        logger.info("")
        logger.info("")
        return


    def _cloudwatch_metrics_cached(self, ec2_obj):
        """
        Raises NoCloudwatchException if no data found in cloudwatch
        """
        df_cw3 = self.cloudwatchman.handle_main({'Region': ec2_obj.region_name}, ec2_obj.instance_id, ec2_obj.launch_time)
        return df_cw3


    def _get_ddg_cached(self, ec2_obj):
        # check if we can get datadog data
        if not self.ddg:
          return None

        if not self.ddg.is_configured():
          return None

        # check cache first
        return self.ddg.get_metrics_all(ec2_obj.instance_id)


    def _handle_ec2obj(self, ec2_obj):
        # logger.debug("%s, %s"%(ec2_obj.instance_id, ec2_obj.instance_type))

        # pandas series of CPU utilization, daily max, for 90 days
        df_metrics = self._cloudwatch_metrics_cached(ec2_obj)

        # pandas series of number of cpu's available on the machine over time, past 90 days
        df_type_ts1 = self.cloudtrail_manager.single(ec2_obj)
        if df_type_ts1 is None:
          raise NoCloudtrailException("No cloudtrail data for %s"%ec2_obj.instance_id)

        # this is redundant with the implementation in _cloudwatch_metrics_core,
        # and it's here just in case the cached redis version is not a date,
        # but it's not really worth it to make a full refresh of the cache for this
        # if df_metrics.Timestamp.dtype==dt.date:
        try:
          df_metrics['Timestamp'] = df_metrics.Timestamp.dt.date
        except AttributeError:
          pass

        # convert type timeseries to the same timeframes as pcpu and n5mn
        #if ec2_obj.instance_id=='i-069a7808addd143c7':
        ec2_df = mergeSeriesOnTimestampRange(df_metrics, df_type_ts1)
        #logger.debug("\nafter merge series on timestamp range")
        #logger.debug(ec2_df.head())

        # merge with type changes (can't use .merge on timestamps, need to use .concat)
        #ec2_df = df_metrics.merge(df_type_ts2, left_on='Timestamp', right_on='EventTime', how='left')
        # ec2_df = pd.concat([df_metrics, df_type_ts2], axis=1)

        # merge with catalog
        ec2_df = ec2_df.merge(self.df_cat[['API Name', 'cost_hourly']], left_on='instanceType', right_on='API Name', how='left')
        #logger.debug("\nafter merge with catalog")
        #logger.debug(ec2_df.head())

        # calculate number of running hours
        # In the latest 90 days, sampling is per minute in cloudwatch
        # https://aws.amazon.com/cloudwatch/faqs/
        # Q: What is the minimum resolution for the data that Amazon CloudWatch receives and aggregates?
        # A: ... For example, if you request for 1-minute data for a day from 10 days ago, you will receive the 1440 data points ...
        ec2_df['nhours'] = np.ceil(ec2_df.SampleCount/60)

        # check if we can get datadog data
        ddg_df = self._get_ddg_cached(ec2_obj)
        # print("ddg data", ddg_df)

        if ddg_df is not None:
          # convert from datetime to date to be able to merge with ec2_df
          ddg_df['ts_dt'] = ddg_df.ts_dt.dt.date
          # append the datadog suffix
          ddg_df = ddg_df.add_suffix('.datadog')
          # merge
          ec2_df = ec2_df.merge(ddg_df, how='outer', left_on='Timestamp', right_on='ts_dt.datadog')

        return ec2_df, ddg_df
