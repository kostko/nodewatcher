import copy
import json

from django import forms
from django.core import exceptions
from django.db import transaction

from registry import cgm
from registry import rules as registry_rules
from registry import registration
from registry.cgm import base as cgm_base

class RegistryMetaForm(forms.Form):
  def __init__(self, items, selected_item = None, *args, **kwargs):
    """
    Class constructor.
    """
    super(RegistryMetaForm, self).__init__(*args, **kwargs)
    
    if selected_item is None:
      selected_item = items.values()[0]
    selected_item = selected_item._meta.module_name
    
    self.fields['item'] = forms.TypedChoiceField(
      choices = [(name, item.RegistryMeta.registry_name) for name, item in items.iteritems()],
      coerce = str,
      initial = selected_item,
      widget = forms.Select(attrs = { 'class' : 'regact_item_chooser' }) if len(items) > 1 else forms.HiddenInput
    )
    self.fields['prev_item'] = forms.TypedChoiceField(
      choices = [(name, item.RegistryMeta.registry_name) for name, item in items.iteritems()],
      coerce = str,
      initial = selected_item,
      widget = forms.HiddenInput
    )

class RegistrySetMetaForm(forms.Form):
  form_count = forms.IntegerField(
    min_value = 0,
    max_value = 10,
    widget = forms.HiddenInput
  )

def generate_form_for_class(regpoint, root, items, prefix, data, index, instance = None, validate = False, partial = None,
                            current_config = None):
  """
  A helper function for generating a form for a specific registry item class.
  """
  validation_errors = False
  selected_item = instance.__class__ if instance is not None else None
  previous_item = None
  
  # Parse a form that holds the item selector
  meta_form = RegistryMetaForm(items, selected_item, data = data, prefix = prefix)
  if validate:
    if not meta_form.is_valid():
      validation_errors = True
    else:
      selected_item = items.get(meta_form.cleaned_data['item'])
      previous_item = items.get(meta_form.cleaned_data['prev_item'])
  
  # Fallback to default item in case of severe problems (this should not happen in normal
  # operation, but might happen when someone tampers with the form)
  if selected_item is None:
    selected_item = items.values()[0]
  
  # Items have changed between submissions, we should copy some field values from the
  # previous form to the new one
  if previous_item is not None and selected_item != previous_item:
    pform = previous_item.get_form()(
      data,
      prefix = prefix + '_' + previous_item._meta.module_name
    )
    
    # Perform a partial clean and copy all valid fields to the new form
    pform.cleaned_data = {}
    pform._errors = {}
    pform._clean_fields()
    initial = {}
    for field in pform.cleaned_data.keys():
      data[prefix + '_' + selected_item._meta.module_name + '-' + field] = \
        data[prefix + '_' + previous_item._meta.module_name + '-' + field]
  
  # When there is no instance, we should create one so we will be able to save somewhere
  if validate and partial is None and instance is None:
    instance = selected_item(root = root)
  
  # Now generate a form for the selected item
  form = selected_item.get_form()(
    data,
    instance = instance,
    prefix = prefix + '_' + selected_item._meta.module_name
  )
  
  # Enable forms to modify themselves accoording to current context
  if hasattr(form, 'modify_to_context') and partial is None:
    if current_config is not None:
      item = current_config[selected_item.RegistryMeta.registry_id][index]
    else:
      item = instance
      current_config = regpoint.get_accessor(root).to_partial()
    
    form.modify_to_context(item, current_config)
  
  if validate:
    if partial is None:
      # Perform a full validation and save the form
      if form.is_valid():
        form.save()
      else:
        validation_errors = True
    else:
      # We are only interested in all the current values even if they might be incomplete
      # and/or invalid, so we can't do full form validation
      form.cleaned_data = {}
      form._errors = {}
      form._clean_fields()
      config = selected_item()
      partial.setdefault(selected_item.RegistryMeta.registry_id, []).append(config)
      
      for field, value in form.cleaned_data.iteritems():
        setattr(config, field, value)
  
  # Generate a new meta form, since the previous item has now changed
  meta_form = RegistryMetaForm(items, selected_item, prefix = prefix)
  return form, meta_form, validation_errors

def prepare_forms_for_regpoint_root(regpoint, root = None, data = None, save = False, only_rules = False, also_rules = False, actions = None,
                                    current_config = None):
  """
  Prepares a list of configuration forms for use on a regpoint root's
  configuration page.
  
  @param regpoint: Registration point name or instance
  @param root: Registration point root instance for which to generate forms
  @param data: User-supplied POST data
  @param save: Are we performing a save or rendering an initial form
  @param only_rules: Should only rules be evaluated and a partial config generated
  @param also_rules: Should rules be evaluated (use only for initial state)
  @param actions: A list of actions that can modify forms
  @param current_config: An existing partial config dictionary
  """
  if save and only_rules:
    raise ValueError("You cannot use save and only_rules at the same time!")
  
  if isinstance(regpoint, basestring):
    regpoint = registration.point(regpoint)
  
  # Transform data into a mutable dictionary in case an immutable one is passed
  data = copy.copy(data)
  actions = actions if actions is not None else {}
  
  try:
    sid = transaction.savepoint()
    validation_errors = False
    forms = []
    partial_config = {} if only_rules else None
    
    for _, items in regpoint.item_list.iteritems():
      cls_meta = items.values()[0].RegistryMeta
      base_prefix = "reg_" + cls_meta.registry_id.replace('.', '_')
      subforms = []
      
      if getattr(cls_meta, 'multiple', False):
        # This is an item class that supports multiple objects of the same class
        if not save and not only_rules:
          # We are generating forms for first-time display purpuses, only include forms for
          # existing models
          for index, mdl in enumerate(regpoint.get_accessor(root).by_path(cls_meta.registry_id)):
            form_prefix = base_prefix + '_mu_' + str(index)
            form, meta_form, has_errors = generate_form_for_class(
              regpoint,
              root,
              items,
              form_prefix,
              None,
              index,
              instance = mdl,
              current_config = current_config
            )
            
            subforms.append({
              'prefix'  : form_prefix,
              'meta'    : meta_form,
              'form'    : form 
            })
          
          # Create the form that contains metadata for this formset
          submeta = RegistrySetMetaForm(
            data,
            prefix = base_prefix + '_sm',
            initial = { 'form_count' : len(subforms) }
          )
        else:
          # We are saving or preparing to evaluate rules, so we regenerate all items
          regpoint.get_accessor(root).by_path(cls_meta.registry_id, queryset = True).delete()
          submeta = RegistrySetMetaForm(
            data,
            prefix = base_prefix + '_sm'
          )
          if not submeta.is_valid():
            raise exceptions.SuspiciousOperation
          
          # TODO implement 'assign' action (its order is not relevant)
          
          # Generate the right amount of forms
          index = 0
          for index in xrange(submeta.cleaned_data['form_count']):
            form_prefix = base_prefix + '_mu_' + str(index)
            form, meta_form, has_errors = generate_form_for_class(
              regpoint,
              root,
              items,
              form_prefix,
              data,
              index,
              validate = True,
              partial = partial_config,
              current_config = current_config
            )
            
            # Check for validation errors, so we don't commit in case of errors
            if has_errors:
              validation_errors = True
            
            subforms.append({
              'prefix'  : form_prefix,
              'meta'    : meta_form,
              'form'    : form 
            })
          
          # Check for any append/clear_config actions and execute them
          meta_modified = False
          index += 1
          # TODO maybe these actions should be abstracted
          for action in actions.get(cls_meta.registry_id, []):
            if action[0] == 'clear_config':
              index = 0
              subforms = []
              meta_modified = True
            elif action[0] == 'append':
              form_prefix = base_prefix + '_mu_' + str(index)
              mdl = action[1] if action[1] is not None else items.values()[0].top_model()
              mdl = mdl(root = root, **action[2])
              
              form, meta_form, has_errors = generate_form_for_class(
                regpoint,
                root,
                items,
                form_prefix,
                None,
                index,
                instance = mdl,
                validate = True,
                current_config = current_config
              )
              index += 1
              meta_modified = True
              
              subforms.append({
                'prefix'  : form_prefix,
                'meta'    : meta_form,
                'form'    : form 
              })
            elif action[0] == 'remove_last' and index > 0:
              index -= 1
              subforms.pop()
              meta_modified = True
          
          if meta_modified:
            # If a form has been modified we cannot simply save, so we fake a validation
            # error so the user will be displayed the updated forms
            validation_errors = True
            
            # Update the submeta form with new count
            submeta = RegistrySetMetaForm(
              prefix = base_prefix + '_sm',
              initial = { 'form_count' : index }
            ) 
      else:
        # This item class only supports a single object to be selected
        if not save and not only_rules:
          mdl = regpoint.get_accessor(root).by_path(cls_meta.registry_id)
        else:
          regpoint.get_accessor(root).by_path(cls_meta.registry_id, queryset = True).delete()
          mdl = None
        
        form_prefix = base_prefix + '_mu_0'
        form, meta_form, has_errors = generate_form_for_class(
          regpoint,
          root,
          items,
          form_prefix,
          data,
          0,
          instance = mdl,
          validate = save or only_rules,
          partial = partial_config,
          current_config = current_config
        )
        
        # Check for validation errors, so we don't commit in case of errors
        if has_errors:
          validation_errors = True
        
        submeta = None
        subforms.append({
          'prefix'  : form_prefix,
          'meta'    : meta_form,
          'form'    : form
        })
      
      forms.append({
        'name'        : cls_meta.registry_section,
        'id'          : cls_meta.registry_id,
        'multiple'    : getattr(cls_meta, 'multiple', False),
        'subforms'    : subforms,
        'submeta'     : submeta,
        'prefix'      : base_prefix
      })
    
    if only_rules:
      # If only rule validation is requested, we should evaluate rules and then rollback
      # the savepoint in any case; all validation errors are ignored
      actions = registry_rules.evaluate(regpoint, root, json.loads(data['STATE']), partial_config)
      transaction.savepoint_rollback(sid)
      return actions, partial_config
    elif also_rules:
      actions = registry_rules.evaluate(regpoint, root, {}, {})
    
    # XXX this should be somewhere else as it is only for nodes! something like
    #     pre-save additional validation (registration?)
    if save and root is not None and not validation_errors:
      # When save is enabled, we must perform node configuration validation
      try:
        # XXX root -> node
        cgm.generate_config(root, only_validate = True)
      except cgm_base.ValidationError, e:
        validation_errors = True
        # TODO Handle validation errors
        raise
    
    if not validation_errors:
      transaction.savepoint_commit(sid)
    else:
      transaction.savepoint_rollback(sid)
  except cgm_base.ValidationError:
    transaction.savepoint_rollback(sid)
  except:
    transaction.savepoint_rollback(sid)
    raise
  
  # Also return (initial) evaluation state when requested
  if also_rules:
    return forms, actions["STATE"]
  
  return forms if not save else (validation_errors, forms)

