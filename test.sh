#!/bin/sh

set -e
set -x

# set caching
ISITFIT_REDIS_HOST=localhost
ISITFIT_REDIS_PORT=6379
ISITFIT_REDIS_DB=0

# clear caching
rm -rf /tmp/isitfit_ec2info.cache
redis-cli -n $ISITFIT_REDIS_DB flushdb #  || echo "redis db clear failed" (eg db number out of range)


# start
echo "Test 0a: version runs ok"
isitfit --version

echo "Test 0b: version takes less than 1 sec (visual check ATM)"
time isitfit --version

echo "Test 1: default profile (shadiakiki1986@gmail.com@amazonaws.com)"
isitfit

echo "Test 2: non-default profile (shadi@autofitcloud.com@amazonaws.com)"
AWS_PROFILE=autofitcloud AWS_DEFAULT_REGION=eu-central-1 isitfit

echo "Test 3: default profile in region with 0 ec2 instances"
AWS_DEFAULT_REGION=eu-central-1 isitfit

echo "Test 4: optimize with default profile"
isitfit --optimize

echo "Test 5: optimize in region with 0 ec2 instances"
AWS_DEFAULT_REGION=eu-central-1 isitfit --optimize

echo "Test 6: optimize with n=1"
isitfit --optimize --n=1

echo "Test 7: {analyse,optimize} filter-tags {ffa,inexistant}"
isitfit --optimize --filter-tags=ffa
isitfit --filter-tags=ffa

isitfit --optimize --filter-tags=inexistant
isitfit --filter-tags=inexistant

echo "Test 8: tags dump"
isitfit tags dump

# done
# `set -x` doesn't let the script reach this point in case of any error
echo "Tests completed"
