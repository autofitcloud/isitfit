import logging
logger = logging.getLogger('isitfit')

import click

from ..utils import display_footer


@click.group(help="Evaluate AWS EC2 costs", invoke_without_command=False)
def cost():
  pass


@cost.command(help='Analyze AWS EC2 cost')
@click.option('--filter-tags', default=None, help='filter instances for only those carrying this value in the tag name or value')
def analyze(filter_tags):
    # gather anonymous usage statistics
    from ..utils import ping_matomo, IsitfitError
    ping_matomo("/cost/analyze")

    #logger.info("Is it fit?")
    try:
      logger.info("Initializing...")

      # moved these imports from outside the function to inside it so that `isitfit --version` wouldn't take 5 seconds due to the loading
      from ..cost.mainManager import MainManager
      from ..cost.utilizationListener import UtilizationListener
      from ..cost.optimizerListener import OptimizerListener
      from ..cost.datadogManager import DatadogManager

      ul = UtilizationListener()
      ddg = DatadogManager()
      mm = MainManager(ddg, filter_tags)

      # utilization listeners
      mm.add_listener('ec2', ul.per_ec2)
      mm.add_listener('all', ul.after_all)
      mm.add_listener('all', ul.display_all)

      # start download data and processing
      logger.info("Fetching history...")
      mm.get_ifi()

    except IsitfitError as e_info:
      logger.error("Error: %s"%str(e_info))
      import sys
      sys.exit(1)

    finally:
      display_footer()


@cost.command(help='Generate recommendations of optimal EC2 sizes')
@click.option('--n', default=0, help='number of underused ec2 optimizations to find before stopping. Skip to get all optimizations')
@click.option('--filter-tags', default=None, help='filter instances for only those carrying this value in the tag name or value')
def optimize(n, filter_tags):
    # gather anonymous usage statistics
    from ..utils import ping_matomo, IsitfitError
    ping_matomo("/cost/optimize")

    #logger.info("Is it fit?")
    try:
      logger.info("Initializing...")

      # moved these imports from outside the function to inside it so that `isitfit --version` wouldn't take 5 seconds due to the loading
      from ..cost.mainManager import MainManager
      from ..cost.utilizationListener import UtilizationListener
      from ..cost.optimizerListener import OptimizerListener
      from ..cost.datadogManager import DatadogManager

      ol = OptimizerListener(n)
      ddg = DatadogManager()
      mm = MainManager(ddg, filter_tags)

      # utilization listeners
      mm.add_listener('pre', ol.handle_pre)
      mm.add_listener('ec2', ol.per_ec2)
      mm.add_listener('all', ol.after_all)
      mm.add_listener('all', ol.storecsv_all)
      mm.add_listener('all', ol.display_all)


      # start download data and processing
      logger.info("Fetching history...")
      mm.get_ifi()

    except IsitfitError as e_info:
      logger.error("Error: %s"%str(e_info))
      import sys
      sys.exit(1)

    finally:
      display_footer()

