"""One application resource cache, shared across tabs/reruns."""

import atexit

import streamlit as st

from workbench.core.config import load_config
from workbench.systems.runner import ApplicationCache, application_key


@st.cache_resource
def _application_cache():
    cache = ApplicationCache()
    atexit.register(cache.close)
    return cache


def get_application(config=None):
    config = config or load_config()
    # Explicit model/config/snapshot identity participates in cache lookup;
    # services and mutable config are never hidden underscore cache arguments.
    return _application_cache().get(config, application_key(config))
