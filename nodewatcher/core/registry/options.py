import collections

from django import forms
from django.core import exceptions as django_exceptions


class Options(object):
    """
    Configuration for registy items.
    """

    def __init__(self, model_class):
        """
        Class constructor.

        :param model_class: Owning registry item class
        """

        meta = model_class.RegistryMeta

        self.registration_point = None
        self.model_class = model_class
        self.registry_id = meta.registry_id
        self.registry_name = getattr(meta, 'registry_name', None)
        self.registry_section = getattr(meta, 'registry_section', None)
        self.multiple = getattr(meta, 'multiple', False)
        self.multiple_static = getattr(meta, 'multiple_static', False)
        self.form_class = None
        self.form_weight = getattr(meta, 'form_weight', 0)
        self.hidden = getattr(meta, 'hidden', False)
        self.hides_parent = getattr(meta, 'hides_parent', False)
        self.hide_requests = 0
        self.lookup_proxies = getattr(meta, 'lookup_proxies', [])
        self.item_children = collections.OrderedDict()
        self.item_parent = None
        self.item_parent_field = None

    def set_form_class(self, form_class):
        """
        Sets the form class that should be used for generating forms for this
        registry item.

        :param form_class: Form class
        """

        self.form_class = form_class

    def get_form_class(self):
        """
        Returns the form class used for generating forms for this registry item.
        """

        if self.form_class is None:
            class DefaultRegistryItemForm(forms.ModelForm):
                class Meta:
                    model = self.model_class
                    fields = '__all__'

            form = DefaultRegistryItemForm
        else:
            form = self.form_class

        return form

    def add_child_item(self, item, field):
        """
        Adds a child registry item class.

        :param item: Child registry item class
        :param field: Model field that is linking the child to the parent
        """

        item_dict = self.item_children.setdefault(item._registry.registry_id, {})
        item_dict[item._meta.model_name] = item

        # Sort children based on form weight.
        self.item_children = collections.OrderedDict(
            sorted(self.item_children.items(), key=lambda x: x[1].values()[0]._registry.form_weight)
        )

        # Setup the parent relation and verify that one doesn't already exist.
        existing_parent = item._registry.item_parent
        if existing_parent is not None and existing_parent._registry.registry_id != self.registry_id:
            raise django_exceptions.ImproperlyConfigured("Registry item cannot have two object parents without a common ancestor!")

        item._registry.item_parent = self.model_class
        item._registry.item_parent_field = field

    def has_parent(self):
        """
        Returns true if this registry item has a parent class.
        """

        return self.item_parent is not None

    def has_children(self):
        """
        Returns true if this registry item has any child classes.
        """

        return len(self.item_children) > 0

    def get_toplevel_class(self):
        """
        Returns the toplevel class for this registry id.
        """

        return self.registration_point.get_top_level_class(self.registry_id)

    def is_toplevel_class(self):
        """
        Returns True if this is the toplevel class for this registry id.
        """

        return self.model_class.__base__ == self.registration_point.item_base

    def get_lookup_chain(self):
        """
        Returns a query filter "chain" that can be used for performing root lookups with
        fields that are part of some registry object.
        """

        if self.is_toplevel_class():
            return '%(namespace)s_%(app_label)s_%(model_name)s' % {
                'namespace': self.registration_point.namespace,
                'app_label': self.model_class._meta.app_label,
                'model_name': self.model_class._meta.model_name,
            }
        else:
            for base in self.model_class.__bases__:
                if hasattr(base, '_registry'):
                    return '%(base_chain)s__%(model_name)s' % {
                        'base_chain': base._registry.get_lookup_chain(),
                        'model_name': self.model_class._meta.model_name,
                    }

    def is_hidden(self):
        """
        Returns True if this registry item class should be hidden.
        """

        return (self.hidden and self.is_toplevel_class()) or self.hide_requests > 0
