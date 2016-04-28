"""
A module for managing methods, specs, requirements, and for starting jobs.
"""
__author__ = "Bill Riehl <wjriehl@lbl.gov>"

from .job import Job
from biokbase.narrative_method_store.client import NarrativeMethodStore
from biokbase.workspace.client import Workspace
import os
import biokbase.auth
from biokbase.narrative.common.url_config import URLS
from .method_util import (
    method_version_tags,
    check_tag,
    system_variable
)
from IPython.display import HTML
from jinja2 import Template
import json
import re

class MethodManager(object):
    nms = NarrativeMethodStore(URLS.narrative_method_store)
    ws_client = Workspace(URLS.workspace)
    method_specs = dict()

    def __init__(self):
        self.reload_methods()

    def check_method(self, method_id, tag='release', raise_exception=False):
        """
        Checks if a method (and release tag) is available for running and such.
        If raise_exception==True, and either the tag or method_id are invalid, a ValueError is raised.
        If raise_exception==False, and there's something invalid, it just returns False.
        If everything is hunky-dory, it returns True.
        """
        tag_ok = check_tag(tag, raise_exception=raise_exception)
        if not tag_ok:
            return False

        if method_id not in self.method_specs[tag]:
            if raise_exception:
                raise ValueError('Unknown method id "{}" tagged as "{}"'.format(method_id, tag))
            return False

        return True

    def reload_methods(self):
        """
        Reloads all method specs into memory from the latest update.
        """
        for tag in method_version_tags:
            specs = self.nms.list_methods_spec({'tag': tag})

            spec_dict = dict()
            for spec in specs:
                spec_dict[spec['info']['id']] = spec
            self.method_specs[tag] = spec_dict

    def method_usage(self, method_id, tag='release'):
        """
        Should show method inputs and outputs. Something like this:
        Method id
        Method name
        Subtitle
        Parameters
            1. xxx - string - data object - [type1, type2, type3]
            (subtitle, description, etc)
            2. yyy - int - from x to y
            (subtitle, description, etc)
            3. zzz - list of strings
            ...
            4. aaa - OUTPUT - ....
        """
        self.check_method(method_id, tag, raise_exception=True)

        spec = self.method_specs[tag][method_id]

        # start with basic info
        usage = {'id': method_id,
                 'name': spec['info']['name'],
                 'tag': tag,
                 'subtitle': spec['info']['subtitle'],
                 'ver': spec['info']['ver'],
                 'params': self._method_params(spec)}

        return MethodUsage(usage)

    def _method_params(self, spec):
        """
        Should return a dict of params with key = id, val =
        {
            optional = boolean,
            is_constant = boolean,
            value = (whatever, optional),
            type = [text|int|float|list],
            is_output = boolean,
            short_hint = string,
            description = string,
            allowed_values = list (optional),
        }
        """
        params = list()
        for p in spec['parameters']:
            p_info = {'id': p['id']}

            if p['optional']==0:
                p_info['optional'] = False
            else:
                p_info['optional'] = True

            p_info['short_hint'] = p['short_hint']
            p_info['description'] = p['description']
            p_info['type'] = p['field_type']
            p_info['is_output'] = False

            if p_info['type'].lower() == 'dropdown':
                p_info['allowed_values'] = [ opt['value'] for opt in p['dropdown_options']['options'] ]

            if 'text_options' in p:
                opts = p['text_options']
                if 'is_output_name' in opts:
                    p_info['is_output'] = opts['is_output_name']
                if 'valid_ws_types' in opts:
                    p_info['allowed_types'] = opts['valid_ws_types']
                if 'validate_as' in opts:
                    p_info['type'] = opts['validate_as']
                if 'min_float' in opts:
                    p_info['min_val'] = opts['min_float']
                if 'min_int' in opts:
                    p_info['min_val'] = opts['min_int']
                if 'max_float' in opts:
                    p_info['max_val'] = opts['max_float']
                if 'max_int' in opts:
                    p_info['max_val'] = opts['max_int']
                if 'regex_constraint' in opts and len(opts['regex_constraint']):
                    p_info['regex_constraint'] = opts['regex_constraint']

            p_info['allow_multiple'] = False
            if p['allow_multiple'] == 1:
                p_info['allow_multiple'] = True

            params.append(p_info)

        return sorted(params, key=lambda p: (p['optional'], p['is_output']))

    def method_description(self, method_id, tag='release'):
        """
        Returns the method description as a printable object. Makes it kinda pretty? repr_html, maybe?
        """
        self.check_method(method_id, tag, raise_exception=True)

        info = self.nms.get_method_full_info({'ids': [method_id], 'tag': tag})[0]
        return info['description']

    def list_available_methods(self, tag="release"):
        """
        Lists the set of available methods in a pretty HTML way.
        Usable only in the Jupyter notebook.
        """
        check_tag(tag, raise_exception=True)

        tmpl="""
        <b>Available {{tag}} methods</b><br>
        <table class="table table-striped table-bordered table-condensed">
        <thead>
            <tr>
                <th>Id</th>
                <th>Name</th>
                <th>Subtitle</th>
            </tr>
        </thead>
        {% for m in methods %}
            <tr>
                <td>
                    {{ m.info.id }}
                </td>
                <td>
                    {{ m.info.name }}
                </td>
                <td>
                    {{ m.info.subtitle }}
                </td>
            </tr>
        {% endfor %}
        </table>
        """

        return HTML(Template(tmpl).render(tag=tag,
                                          methods=sorted(list(self.method_specs[tag].values()), key=lambda m: m['info']['id'])))


    def run_method(self, method_id, tag="release", version=None, **kwargs):
        """
        Attemps to run the method, returns a Job with the running method info.
        Should *hopefully* also inject that method into the Narrative's metadata.
        Probably need some kind of JavaScript-foo to get that to work.

        Parameters:
        -----------
        method_id - should be from the method spec, e.g. 'build_a_metabolic_model'
                    or 'MegaHit/run_megahit'.
        tag - optional, one of [release|beta|dev] (default=release)
        version - optional, a semantic version string. Only released modules have
                  versions, so if the tag is not 'release', and a version is given,
                  a ValueError will be raised.
        **kwargs - these are the set of parameters to be used with the method.
                   They can be found by using the method_usage function. If any
                   non-optional methods are missing, a ValueError will be raised.

        Example:
        --------
        my_job - mm.run_method('MegaHit/run_megahit', version=">=1.0.0", read_library_name="My_PE_Library", output_contigset_name="My_Contig_Assembly")
        """

        # Intro tests:
        self.check_method(method_id, tag, raise_exception=True)

        if version is not None and tag != "release":
            raise ValueError("Method versions only apply to released method modules!")

        # Get the spec & params
        spec = self.method_specs[tag][method_id]
        spec_params = self._method_params(spec)

        # Preflight check the params - all required ones are present, all values are the right type, all numerical values are in given ranges

        spec_param_ids = [ p['id'] for p in spec_params ]

        # First, test for presence.
        missing_params = list()
        for p in spec_params:
            if not p['optional'] and not kwargs.get(p['id'], None):
                missing_params.append(p['id'])
        if len(missing_params):
            raise ValueError('Missing required parameters {} - try executing method_usage("{}", tag="{}") for more information'.format(json.dumps(missing_params), method_id, tag))

        # Next, test for extra params that don't make sense
        extra_params = list()
        for p in kwargs.keys():
            if p not in spec_param_ids:
                extra_params.append(p)
        if len(extra_params):
            raise ValueError('Unknown parameters {} - maybe something was misspelled?\nexecute method_usage("{}", tag="{}") for more information'.format(json.dumps(extra_params), method_id, tag))

        # Now, test for values.
        # Should also check if input (NOT OUTPUT) object variables are present in the current workspace

        workspace = system_variable('workspace')
        param_errors = list()
        for p in spec_params:
            if p['id'] in kwargs:
                err = self._check_parameter(p, kwargs[p['id']], workspace)
                if err is not None:
                    param_errors.append("{} - {}".format(p['id'], err))
        if len(param_errors):
            raise ValueError('Parameter value errors found! {}'.format(json.dumps(param_errors)))

        return None

    def _check_parameter(self, param, value, workspace):
        if param['allow_multiple'] and isinstance(value, list):
            error_list = list()
            for v in value:
                err = self._validate_param_value(param, v, workspace)
                if err:
                    error_list.append(err)
            if len(error_list):
                return ", ".join(error_list)
            else:
                return None
        return self._validate_param_value(param, value, workspace)


    def _validate_param_value(self, param, value, workspace):
        """
        Tests a value to make sure it's valid.
        Returns None if valid, an error string if not.
        """

        # cases - value == list, int, float, others get rejected
        if not (isinstance(value, basestring) or
                isinstance(value, int) or
                isinstance(value, float)):
            return "input type not supported - only str, int, float, or list"

        # check types. basestring is pretty much anything (it'll just get casted),
        # but ints, floats, or lists are funky.
        if param['type'] == 'int' and not isinstance(value, int):
            return 'Given value {} is not an int'.format(value)
        elif param['type'] == 'float' and not (isinstance(value, float) or isinstance(value, int)):
            return 'Given value {} is not a number'.format(value)

        # if it's expecting a workspace object, check if that's present, and a valid type
        if 'allowed_types' in param and not param['is_output']:
            try:
                info = self.ws_client.get_object_info_new({'objects': [{'workspace':workspace, 'name':value}]})[0]
                type_ok = False
                for t in param['allowed_types']:
                    if re.match(t, info[2]):
                        type_ok = True
                if not type_ok:
                    return 'Type of data object, {}, does not match allowed types'.format(info[2])
            except Exception as e:
                return 'Data object named {} not found with this Narrative. (additional info: {})'.format(value, e)

        # if it expects a set of allowed values, check if this one matches
        if 'allowed_values' in param:
            if value not in param['allowed_values']:
                return "Given value is not permitted in the allowed set."

        # if it expects a numerical value in a certain range, check that.
        if 'max_val' in param:
            try:
                if float(value) > param['max_val']:
                    return "Given value {} should be <= {}".format(value, param['max_val'])
            except:
                return "Given value {} must be a number".format(value)

        if 'min_val' in param:
            try:
                if float(value) < param['min_val']:
                    return "Given value {} should be >= {}".format(value, param['min_val'])
            except:
                return "Given value {} must be a number".format(value)

        # Last, regex. not being used, but... eh.
        if 'regex_constraint' in param:
            for regex in regex_constraint:
                if not re.match(regex_constraint, value):
                    return 'Value {} does not match required regex {}'.format(value, regex)

        # Whew. Passed all filters!
        return None


class MethodUsage(object):
    def __init__(self, usage):
        self.usage = usage

    def _repr_html_(self):
        tmpl = """
        <h1>{{usage.name}}</h1>
        id = {{usage.id}}<br>
        {{usage.subtitle}}<br>
        Parameters (<span class="bg-warning">required</span>)
        <table class="table table-striped table-bordered table-condensed">
        <thead>
            <tr>
                <th>Id</th>
                <th>Type</th>
                <th>Allowed Types</th>
                <th>Description</th>
                <th>Allowed Values</th>
            </tr>
        </thead>
        {% for p in usage.params %}
            <tr {% if not p.optional %}class="warning"{% endif %}>
            <td>{{ p.id|e }}</td>
            <td>{{ p.type|e }}{% if p.is_output %} (output){% endif %}</td>
            <td>
            {% if p.allowed_types %}
                {% for t in p.allowed_types %}
                    {{t}}<br>
                {% endfor %}
            {% endif %}
            </td>
            <td>{{ p.short_hint|e }}</td>
            <td>
            {% if p.allowed_values %}
                {% for v in p.allowed_values %}
                    {{v}}<br>
                {% endfor %}
            {% endif %}
            </td>
            </tr>
        {% endfor %}
        </table>
        """

        return Template(tmpl).render(usage=self.usage)

        # return "<h1>" + self.usage['name'] + "</h1>" + self.usage['id'] + "<br>"

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        s = "id: {}\nname: {}\nsubtitle: {}\nparameters (*required):\n-----------------------".format(self.usage['id'], self.usage['name'], self.usage['subtitle'])

        for p in self.usage['params']:
            if not p.get("is_constant", False):
                p_def = "\n{}{} - {}".format('*' if not p['optional'] else '', p['id'], p['type'])
                if "allowed_types" in p:
                    p_def = p_def + " - is a data object where the type is one of: {}".format(json.dumps(p['allowed_types']))
                if "allowed_values" in p:
                    p_def = p_def + " - must be one of {}".format(json.dumps(p['allowed_values']))
                s = s + p_def

        return s