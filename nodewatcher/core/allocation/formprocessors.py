from ..registry import registration
from ..registry.forms import formprocessors

from . import models


class AutoPoolAllocator(formprocessors.RegistryFormProcessor):
    """
    A form processor that attempts to automatically satisfy allocation
    requests defined by AddressAllocator config items.
    """

    def __init__(self):
        """
        Class constructor.
        """

        self.allocations = set()

    def preprocess(self, node):
        """
        Performs preprocessing of allocations for `node`.
        """

        if node is None:
            # A new node is being registered, so we have nothing to add here
            return

        # Automatically discover currently available allocation sources
        allocation_sources = [
            item for item in registration.point('node.config').config_items() if issubclass(item, models.AddressAllocator)
        ]

        for src in allocation_sources:
            for allocation in node.config.by_registry_id(src._registry.registry_id):
                if isinstance(allocation, src):
                    self.allocations.add(allocation)

    def postprocess(self, node):
        """
        Automatically satisfy allocation requests for `node`.
        """

        # Automatically discover currently available allocation sources
        allocation_sources = [
            item for item in registration.point('node.config').config_items() if issubclass(item, models.AddressAllocator)
        ]

        unsatisfied_requests = []
        for src in allocation_sources:
            for request in node.config.by_registry_id(src._registry.registry_id):
                if isinstance(request, src):
                    if not request.is_satisfied():
                        unsatisfied_requests.append(request)
                    else:
                        for allocation in self.allocations.copy():
                            if allocation.exactly_matches(request):
                                self.allocations.remove(allocation)
                                break

        # Attempt to reuse existing resources before requesting new ones
        for request in unsatisfied_requests:
            for unused in self.allocations.copy():
                if request.satisfy_from(unused):
                    self.allocations.remove(unused)
                    break
            else:
                request.free()
                request.satisfy(node)

                # Prevent the allocation from being freed
                if request in self.allocations:
                    self.allocations.remove(request)

        # Free existing unused resources
        # TODO: Do this only when saving for real, not on validation runs
        for unused in self.allocations:
            unused.free()

registration.point('node.config').add_form_processor(AutoPoolAllocator, order=100)
