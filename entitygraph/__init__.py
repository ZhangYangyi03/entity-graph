"""entity-graph: a company entity graph built only from sources that need no paid key.

Every node and edge carries its provenance: which source produced it, the URL that was
actually fetched, and when. An edge without a source is not allowed into the graph --
that rule is what separates a citation from a guess.
"""
__version__ = "0.1.0"

from .model import Node, Edge, Graph
from .resolve import Resolver
from .build import build

__all__ = ["Node", "Edge", "Graph", "Resolver", "build", "__version__"]
