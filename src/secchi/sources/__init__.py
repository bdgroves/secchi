"""Data-source clients.

Each submodule wraps one upstream data provider behind a small, uniform
interface: a ``list_sensors()``-shaped discovery call plus one or more
``fetch_*`` methods that return raw records the ingest layer archives.
"""
