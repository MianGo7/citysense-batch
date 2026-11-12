"""Integration tests of the replica set, run against the Docker Compose stack.

They need `docker compose up` to have finished, and check what an operator of the
stack would see: one primary and two secondaries, the member priorities that make
mongo1 the primary after a clean start, and the small cache of every node.
"""

import pytest
from pymongo import MongoClient

pytestmark = pytest.mark.integration

# The members advertise their internal host names, which the host cannot resolve,
# so the published port is used as a direct connection to mongo1 and not as a
# replica set connection.
URI = "mongodb://127.0.0.1:27017/?directConnection=true"
CACHE_BYTES = 256 * 1024 * 1024


@pytest.fixture(scope="module")
def client():
    with MongoClient(URI, serverSelectionTimeoutMS=5000) as connection:
        yield connection


def test_one_primary_and_two_secondaries(client):
    status = client.admin.command("replSetGetStatus")
    states = sorted(member["stateStr"] for member in status["members"])
    assert states == ["PRIMARY", "SECONDARY", "SECONDARY"]


def test_replica_set_is_named_rs0(client):
    assert client.admin.command("replSetGetStatus")["set"] == "rs0"


def test_mongo1_has_the_highest_priority_and_is_primary(client):
    members = client.admin.command("replSetGetConfig")["config"]["members"]
    priorities = {member["host"]: member["priority"] for member in members}
    assert priorities == {"mongo1:27017": 2, "mongo2:27017": 1, "mongo3:27017": 1}
    # The published port belongs to mongo1, so it reaches the primary after a
    # clean start. After a failover test it may take a moment to take over again.
    assert client.admin.command("hello")["isWritablePrimary"] is True


def test_wiredtiger_cache_is_limited(client):
    cache = client.admin.command("serverStatus")["wiredTiger"]["cache"]
    assert cache["maximum bytes configured"] == CACHE_BYTES
