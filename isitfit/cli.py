# RuntimeError: Click will abort further execution because Python 3 was configured to use ASCII as encoding for the environment. 
# Consult https://click.palletsprojects.com/en/7.x/python3/ for mitigation steps.
# 
# Edit 2019-10-08: whatsapp's wadebug uses "click.disable_unicode_literals_warning = True"
# Ref: https://github.com/WhatsApp/WADebug/blob/958ac37be804cc732ae514d4872b93d19d197a5c/wadebug/cli.py#L23
from .utils import mysetlocale
mysetlocale()


import logging
logger = logging.getLogger('isitfit')

import click

from . import isitfit_version

def display_footer():
    logger.info("")
    logger.info("Generated by isitfit version %s"%isitfit_version)
    logger.info("For more info about isitfit, check https://isitfit.autofitcloud.com ⛅")
    logger.info("Also, consider following the Global Climate Strike news at https://twitter.com/hashtag/ClimateStrike 🌎")

# With atexit, this message is being displayed even in case of early return or errors.
# Changing to try/finally in the __main__ below
#import atexit
#atexit.register(display_footer)


@click.group(invoke_without_command=True)
@click.option('--debug', is_flag=True, help='Display more details to help with debugging')
@click.option('--version', is_flag=True, help='Show the installed version')
@click.option('--optimize', is_flag=True, help='Generate recommendations of optimal EC2 sizes')
@click.option('--n', default=0, help='number of underused ec2 optimizations to find before stopping. Skip to get all optimizations')
@click.option('--filter-tags', default=None, help='filter instances for only those carrying this value in the tag name or value')
@click.pass_context
def cli(ctx, debug, version, optimize, n, filter_tags):
    if version:
      print('isitfit version %s'%isitfit_version)
      return

    logLevel = logging.DEBUG if debug else logging.INFO
    ch = logging.StreamHandler()
    ch.setLevel(logLevel)
    logger.addHandler(ch)
    logger.setLevel(logLevel)

    if debug:
      logger.debug("Enabled debug level")
      logger.debug("-------------------")

    # check if current version is out-of-date
    from .utils import prompt_upgrade
    is_outdated = prompt_upgrade('isitfit', isitfit_version)
    if is_outdated:
      # Give the user some time to read the message and possibly update
      import time
      time.sleep(3)

    # do not continue with the remaining code here
    # if a command is invoked, eg `isitfit tags`
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand is not None:
      return

    # gather anonymous usage statistics
    from .utils import ping_matomo
    if optimize:
      ping_matomo("/cost/optimize")
    else:
      ping_matomo("/cost/analyze")

    #logger.info("Is it fit?")
    from .utils import IsitfitError
    try:
      logger.info("Initializing...")

      # moved these imports from outside the function to inside it so that `isitfit --version` wouldn't take 5 seconds due to the loading
      from .mainManager import MainManager
      from .utilizationListener import UtilizationListener
      from .optimizerListener import OptimizerListener
      from .datadogManager import DatadogManager

      ul = UtilizationListener()
      ol = OptimizerListener(n)
      ddg = DatadogManager()
      mm = MainManager(ddg, filter_tags)

      # utilization listeners
      if not optimize:
        mm.add_listener('ec2', ul.per_ec2)
        mm.add_listener('all', ul.after_all)
        mm.add_listener('all', ul.display_all)
      else:
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

#-----------------------

@cli.group(help="Explore EC2 tags", invoke_without_command=False)
def tags():
  pass



@tags.command(help="Generate new tags suggested by isitfit for each EC2 instance")
@click.option('--advanced', is_flag=True, help='Get advanced suggestions of tags. Requires login')
@click.pass_context
def suggest(ctx, advanced):
  # gather anonymous usage statistics
  from .utils import ping_matomo
  ping_matomo("/tags/suggest")

  from .utils import IsitfitError
  tl = None
  if not advanced:
    from .tagsSuggestBasic import TagsSuggestBasic
    tl = TagsSuggestBasic()
  else:
    from .tagsSuggestAdvanced import TagsSuggestAdvanced
    tl = TagsSuggestAdvanced()

  try:
    tl.prepare()
    tl.fetch()
    tl.suggest()
    tl.display()
  except IsitfitError as e:
    logger.error("Error: %s"%str(e))
    import sys
    sys.exit(1)

  display_footer()


@tags.command(help="Dump existing EC2 tags in tabular form into a csv file")
@click.pass_context
def dump(ctx):
  # gather anonymous usage statistics
  from .utils import ping_matomo
  ping_matomo("/tags/dump")

  from .tagsDump import TagsDump
  from .utils import IsitfitError
  tl = TagsDump()

  try:
    tl.fetch()
    tl.suggest() # not really suggesting. Just dumping to csv
    tl.display()
  except IsitfitError as e:
    logger.error("Error: %s"%str(e))
    import sys
    sys.exit(1)

  display_footer()



@tags.command(help="Push EC2 tags from csv file")
@click.argument('csv_filename') #, help='Path to CSV file holding tags to be pushed. Should match format from `isitfit tags dump`')
@click.option('--not-dry-run', is_flag=True, help='True for dry run (simulated push)')
def push(csv_filename, not_dry_run):
  # gather anonymous usage statistics
  from .utils import ping_matomo
  ping_matomo("/tags/push")

  from .tagsPush import TagsPush
  from .utils import IsitfitError

  tp = TagsPush(csv_filename)
  try:
    tp.read_csv()
    tp.validateTagsFile()
    tp.pullLatest()
    tp.diffLatest()
    tp.processPush(not not_dry_run)
  except IsitfitError as e:
    logger.error("Error: %s"%str(e))
    import sys
    sys.exit(1)

  display_footer()



#-----------------------

if __name__ == '__main__':
  cli()
