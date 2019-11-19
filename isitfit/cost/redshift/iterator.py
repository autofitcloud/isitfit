# imports
import datetime as dt
from ...utils import SECONDS_IN_ONE_DAY
import pandas as pd

import logging
logger = logging.getLogger('isitfit')


class BaseIterator:
  """
  Iterator design pattern
  Iterates over all CPU performance dataframes
  https://en.wikipedia.org/wiki/Iterator_pattern#Python
  """

  service_name = None
  service_description = None
  paginator_name = None
  paginator_entryJmespath = None
  paginator_exception = None
  entry_keyId = None
  entry_keyCreated = None


  def __init__(self):
    # list of cluster ID's for which data is not available
    self.rc_noData = []

    # list of regions to skip
    self.region_include = []

    # in case of just_count=True, region_include is ignored since it is not yet populated
    # Set this flag to use region_include, eg if it is loaded from cache or if counting first pass is done
    self.regionInclude_ready = False

    # init cache
    self._initCache()

    # count of entries
    self.n_entry = None


  def _initCache(self):
    """
    # try to load region_include from cache
    """

    # need to use the profile name
    # because a profile could have ec2 in us-east-1
    # whereas another could have ec2 in us-west-1
    import boto3
    profile_name = boto3.session.Session().profile_name

    # cache filename and key to use
    from ...dotMan import DotMan
    import os
    self.cache_filename = 'iterator_cache-%s-%s.pkl'%(profile_name, self.service_name)
    self.cache_filename = os.path.join(DotMan().get_dotisitfit(), self.cache_filename)

    self.cache_key = 'iterator-region_include'

    # https://github.com/barisumog/simple_cache
    import simple_cache
    ri_cached = simple_cache.load_key(filename=self.cache_filename, key=self.cache_key)
    if ri_cached is not None:
      logger.debug("Loading regions containing EC2 from cache file")
      self.region_include = ri_cached
      self.regionInclude_ready = True


  def iterate_core(self, display_tqdm=False):
    fx_l = ['service_name', 'service_description', 'paginator_name', 'paginator_entryJmespath', 'paginator_exception', 'entry_keyId', 'entry_keyCreated']
    for fx_i in fx_l:
      # https://stackoverflow.com/a/9058315/4126114
      if fx_i not in self.__class__.__dict__.keys():
        raise Exception("Derived class should set %s"%fx_i)

    # iterate on regions
    import botocore
    import boto3
    import jmespath
    redshift_regions = boto3.Session().get_available_regions(self.service_name)
    # redshift_regions = ['us-west-2'] # FIXME

    region_iterator = redshift_regions
    if display_tqdm:
      from tqdm import tqdm
      region_iterator = tqdm(region_iterator, total = len(redshift_regions), desc="%s, counting in all regions"%self.service_description)

    for region_name in region_iterator:
      if self.regionInclude_ready:
        if region_name not in self.region_include:
          # skip since already failed to use it
          continue

      logger.debug("Region %s"%region_name)
      boto3.setup_default_session(region_name = region_name)

      # boto3 clients
      # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/redshift.html#Redshift.Client.describe_logging_status
      redshift_client = boto3.client(self.service_name)

      # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/cloudwatch.html#metric
      self.cloudwatch_resource = boto3.resource('cloudwatch')

      # iterate on redshift clusters
      paginator = redshift_client.get_paginator(self.paginator_name)
      rc_iterator = paginator.paginate()
      try:
        region_anyClusterFound = False
        for rc_describe_page in rc_iterator:
          rc_describe_entries = jmespath.search(self.paginator_entryJmespath, rc_describe_page)
          for rc_describe_entry in rc_describe_entries:
            region_anyClusterFound = True
            # add field for region
            rc_describe_entry['Region'] = region_name
            # yield
            yield rc_describe_entry

        if not self.regionInclude_ready:
          if region_anyClusterFound:
            # only include if found clusters in this region
            self.region_include.append(region_name)

      except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code']==self.paginator_exception:
          continue

        # all other exceptions raised
        raise e

    # before exiting, check if a count just completed, and mark region_include as usable
    if not self.regionInclude_ready:
      self.regionInclude_ready = True

      # save to cache
      import simple_cache
      SECONDS_PER_HOUR = 60*60
      simple_cache.save_key(filename=self.cache_filename, key=self.cache_key, value=self.region_include, ttl=SECONDS_PER_HOUR)


  def count(self):
      # method 1
      # ec2_it = self.ec2_resource.instances.all()
      # return len(list(ec2_it))

    if self.n_entry is not None:
      return self.n_entry

    self.n_entry = len(list(self.iterate_core(True)))

    msg_count = "Found a total of %i EC2 instance(s) in %i region(s) (other regions do not hold any EC2)"
    logger.warning(msg_count%(self.n_entry, len(self.region_include)))

    return self.n_entry


  def __iter__(self):
    for rc_describe_entry in self.iterate_core(False):
        #print("response, entry")
        #print(rc_describe_entry)

        # if not available yet (eg creating), still include analysis in case of past data
        #if rc_describe_entry['ClusterStatus'] != 'available':
        #    self.rc_noData.append(rc_id)
        #    continue

        if self.entry_keyId not in rc_describe_entry:
          # no ID, weird
          continue

        rc_id = rc_describe_entry[self.entry_keyId]

        if self.entry_keyCreated not in rc_describe_entry:
          # no creation time yet, maybe in process
          self.rc_noData.append(rc_id)
          continue

        rc_created = rc_describe_entry[self.entry_keyCreated]

        yield rc_describe_entry, rc_id, rc_created



class RedshiftPerformanceIterator(BaseIterator):
  service_name = 'redshift'
  service_description = 'Redshift clusters'
  paginator_name = 'describe_clusters'
  paginator_entryJmespath = 'Clusters[]'
  paginator_exception = 'InvalidClientTokenId'
  entry_keyId = 'ClusterIdentifier'
  entry_keyCreated = 'ClusterCreateTime'


class Ec2Iterator(BaseIterator):
  service_name = 'ec2'
  service_description = 'EC2 instances'
  paginator_name = 'describe_instances'
  # Notice that [] notation flattens the list of lists
  # http://jmespath.org/tutorial.html
  paginator_entryJmespath = 'Reservations[].Instances[]'
  paginator_exception = 'AuthFailure'
  entry_keyId = 'InstanceId'
  entry_keyCreated = 'LaunchTime'

  def __iter__(self):
    # over-ride the __iter__ to get the ec2 resource object for the current code (backwards compatibility)

    # method 1 for ec2
    # ec2_it = self.ec2_resource.instances.all()
    # return ec2_it

    # boto3 ec2 and cloudwatch data
    ec2_resource_all = {}
    import boto3

    # TODO cannot use directly use the iterator exposed in "ec2_it"
    # because it would return the dataframes from Cloudwatch,
    # whereas in the cloudwatch data fetch here, the data gets cached to redis.
    # Once the redshift.iterator can cache to redis, then the cloudwatch part here
    # can also be dropped, as well as using the "ec2_it" iterator directly
    # for ec2_dict in self.ec2_it:
    for ec2_dict, ec2_id, ec2_launctime in super().__iter__():
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

