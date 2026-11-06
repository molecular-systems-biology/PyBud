"""
gui/state.py — shared singleton state for the PyBud GUI.

The single PyBud instance here holds every setting, the loaded image, seeds,
and fitted cells. Every GUI module imports `pybud` from here rather than
constructing its own, so they all share the same live state.
"""
from pybud import PyBud

pybud = PyBud()
