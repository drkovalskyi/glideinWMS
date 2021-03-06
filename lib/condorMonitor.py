#
# Project:
#   glideinWMS
#
# File Version: 
#
# Description:
#   This module implements classes to query the condor daemons
#   and manipulate the results
#   Please notice that it also converts \" into "
#
# Author:
#   Igor Sfiligoi (Aug 30th 2006)
#

import condorExe
import condorSecurity
import os
import string
import copy
import socket
import xml.parsers.expat

#
# Configuration
#

# Set path to condor binaries
def set_path(new_condor_bin_path):
    global condor_bin_path
    condor_bin_path = new_condor_bin_path

#
# Caching classes
#

# dummy caching class, when you don't want caching
# used as base class below, too
class NoneScheddCache:
    #returns (cms arg schedd string,LOCAL_DIR)
    def getScheddId(self,schedd_name,pool_name):
        return (self.iGetCmdScheddStr(schedd_name),{})

    # INTERNAL and for inheritance
    def iGetCmdScheddStr(self,schedd_name):
        if schedd_name is None:
            schedd_str=""
        else:
            schedd_str = "-name %s " % schedd_name

        return schedd_str


# The schedd can be found either through -name attr
# or through the local disk lookup
# Remember which one to use
class LocalScheddCache(NoneScheddCache):
    def __init__(self):
        self.enabled=True
        # dictionary of
        #   (schedd_name,pool_name)=>(cms arg schedd string,env)
        self.cache={}

        self.my_ips=socket.gethostbyname_ex(socket.gethostname())[2]
        try:
            self.my_ips+=socket.gethostbyname_ex('localhost')[2]
        except socket.gaierror,e:
            pass # localhost not defined, ignore
            
                

    def enable(self):
        self.enabled=True

    def disable(self):
        self.enabled=False

    #returns (cms arg schedd string,env)
    def getScheddId(self,schedd_name,pool_name):
        if schedd_name is None: # special case, do not cache
            return ("",{})

        if self.enabled:
            k=(schedd_name,pool_name)
            if not self.cache.has_key(k): # not in cache, discover it
                env=self.iGetEnv(schedd_name, pool_name)
                if env is None: #
                    self.cache[k]=(self.iGetCmdScheddStr(schedd_name),{})
                else:
                    self.cache[k]=("",env)
            return self.cache[k]
        else: # not enabled, just return the str
            return (self.iGetCmdScheddStr(schedd_name),{})

    #
    # PRIVATE
    #

    # return None if not found
    # Can raise exceptions
    def iGetEnv(self,schedd_name, pool_name):
        cs=CondorStatus('schedd',pool_name)
        data=cs.fetch(constraint='Name=?="%s"'%schedd_name,format_list=[('ScheddIpAddr','s'),('SPOOL_DIR_STRING','s'),('LOCAL_DIR_STRING','s')])
        if not data.has_key(schedd_name):
            raise RuntimeError, "Schedd '%s' not found"%schedd_name

        el=data[schedd_name]
        if 'SPOOL_DIR_STRING' not in el and 'LOCAL_DIR_STRING' not in el: # not advertising, cannot use disk optimization
            return None
        if not el.has_key('ScheddIpAddr'): # This should never happen
            raise RuntimeError, "Schedd '%s' is not advertising ScheddIpAddr"%schedd_name

        schedd_ip=el['ScheddIpAddr'][1:].split(':')[0]
        if schedd_ip in self.my_ips: #seems local, go for the dir
            l=el.get('SPOOL_DIR_STRING', el.get('LOCAL_DIR_STRING'))
            if os.path.isdir(l): # making sure the directory exists
                if 'SPOOL_DIR_STRING' in el:
                    return {'_CONDOR_SPOOL': '%s' %l }
                else: # LOCAL_DIR_STRING
                    return {'_CONDOR_SPOOL': '%s/spool' %l }

            else: #dir does not exist, likely not relevant, revert to standard behaviour
                return None
        else: # not local
            return None

# default global object
local_schedd_cache=LocalScheddCache()
      
      
def condorq_attrs(q_constraint, attribute_list):  
    """
    Retrieves a list of a single item from the all the factory queues.
    """
    attr_str = ""
    for attr in attribute_list:
        attr_str += " -attr %s" % attr

    xml_data = condorExe.exe_cmd("condor_q","-g -l %s -xml -constraint '%s'" % (attr_str, q_constraint))
    
    
    classads_xml = []
    tmp_list = []
    for line in xml_data:
        # look for the xml header
        if line[:5] == "<?xml":
            if len(tmp_list) > 0:
                classads_xml.append(tmp_list)
            tmp_list = []
        tmp_list.append(line)
    
    q_proxy_list = []
    for ad_xml in classads_xml:
        cred_list = xml2list(ad_xml)
        q_proxy_list.extend(cred_list) 
                            
    return q_proxy_list
    
#
# Condor monitoring classes
#

# Generic, you most probably don't want to use these
class AbstractQuery: # pure virtual, just to have a minimum set of methods defined
    # returns the data, will not modify self
    def fetch(self,constraint=None,format_list=None):
        raise NotImplementedError,"Fetch not implemented"

    # will fetch in self.stored_data
    def load(self,constraint=None,format_list=None):
        raise NotImplementedError,"Load not implemented"

    # constraint_func is a boolean function, with only one argument (data el)
    # same output as fetch, but limited to constraint_func(el)==True
    #
    # if constraint_func==None, return all the data
    def fetchStored(self,constraint_func=None):
        raise NotImplementedError,"fetchStored not implemented"

class StoredQuery(AbstractQuery): # still virtual, only fetchStored defined
    stored_data = {}
    
    def fetchStored(self,constraint_func=None):
        return applyConstraint(self.stored_data,constraint_func)

#
# format_list is a list of
#  (attr_name, attr_type)
# where attr_type is one of
#  "s" - string
#  "i" - integer
#  "r" - real (float)
#  "b" - bool
#
#
# security_obj, if defined, should be a child of condorSecurity.ProtoRequest
class QueryExe(StoredQuery): # first fully implemented one, execute commands 
    def __init__(self,exe_name,resource_str,group_attribute,pool_name=None,security_obj=None,env={}):
        self.exe_name=exe_name
        self.env=env
        self.resource_str=resource_str
        self.group_attribute=group_attribute
        self.pool_name=pool_name
        if pool_name is None:
            self.pool_str=""
        else:
            self.pool_str = "-pool %s" % pool_name

        if security_obj is not None:
            if security_obj.has_saved_state():
                raise RuntimeError, "Cannot use a security object which has saved state."
            self.security_obj=copy.deepcopy(security_obj)
        else:
            self.security_obj=condorSecurity.ProtoRequest()

    def require_integrity(self,requested_integrity): # if none, dont change, else forse that one
        if requested_integrity is None:
            condor_val=None
        elif requested_integrity:
            condor_val="REQUIRED"
        else:
            # if not required, still should not fail if the other side requires it
            condor_val='OPTIONAL'

        self.security_obj.set('CLIENT','INTEGRITY',condor_val)

    def get_requested_integrity(self):
        condor_val = self.security_obj.get('CLIENT','INTEGRITY')
        if condor_val is None:
            return None
        return (condor_val=='REQUIRED')

    def require_encryption(self,requested_encryption): # if none, dont change, else forse that one
        if requested_encryption is None:
            condor_val=None
        elif requested_encryption:
            condor_val="REQUIRED"
        else:
            # if not required, still should not fail if the other side requires it
            condor_val='OPTIONAL'

        self.security_obj.set('CLIENT','ENCRYPTION',condor_val)

    def get_requested_encryption(self):
        condor_val = self.security_obj.get('CLIENT','ENCRYPTION')
        if condor_val is None:
            return None
        return (condor_val=='REQUIRED')

    def fetch(self,constraint=None,format_list=None):
        if constraint is None:
            constraint_str=""
        else:
            constraint_str="-constraint '%s'"%constraint

        full_xml=(format_list is None)
        if format_list is not None:
            format_arr=[]
            for format_el in format_list:
                attr_name,attr_type=format_el
                attr_format={'s':'%s','i':'%i','r':'%f','b':'%i'}[attr_type]
                format_arr.append('-format "%s" "%s"'%(attr_format,attr_name))
            format_str=string.join(format_arr," ")

        # set environment for security settings
        self.security_obj.save_state()
        try:
            self.security_obj.enforce_requests()

            if full_xml:
                xml_data = condorExe.exe_cmd(self.exe_name,"%s -xml %s %s"%(self.resource_str,self.pool_str,constraint_str),env=self.env);
            else:
                xml_data = condorExe.exe_cmd(self.exe_name,"%s %s -xml %s %s"%(self.resource_str,format_str,self.pool_str,constraint_str),env=self.env);

        finally:
            # restore old values
            self.security_obj.restore_state()

        list_data = xml2list(xml_data)
        del xml_data
        dict_data = list2dict(list_data, self.group_attribute)
        return dict_data

    def load(self, constraint=None, format_list=None):
        self.stored_data = self.fetch(constraint, format_list)


#
# Fully usable query functions
#

# condor_q 
class CondorQ(QueryExe):
    def __init__(self,schedd_name=None,pool_name=None,security_obj=None,schedd_lookup_cache=local_schedd_cache):
        self.schedd_name=schedd_name

        if schedd_lookup_cache is None:
            schedd_lookup_cache=NoneScheddCache()

        schedd_str,env=schedd_lookup_cache.getScheddId(schedd_name, pool_name)

        QueryExe.__init__(self,"condor_q",schedd_str,["ClusterId","ProcId"],pool_name,security_obj,env)

    def fetch(self, constraint=None, format_list=None):
        if format_list is not None:
            # check that ClusterId and ProcId are present, and if not add them
            format_list = complete_format_list(format_list, [("ClusterId", 'i'), ("ProcId", 'i')])
        return QueryExe.fetch(self, constraint=constraint, format_list=format_list)


# condor_q, where we have only one ProcId x ClusterId
class CondorQLite(QueryExe):
    def __init__(self,schedd_name=None,pool_name=None,security_obj=None,schedd_lookup_cache=local_schedd_cache):
        self.schedd_name=schedd_name

        if schedd_lookup_cache is None:
            schedd_lookup_cache=NoneScheddCache()

        schedd_str,env=schedd_lookup_cache.getScheddId(schedd_name, pool_name)

        QueryExe.__init__(self,"condor_q",schedd_str,"ClusterId",pool_name,security_obj,env)

    def fetch(self, constraint=None, format_list=None):
        if format_list is not None:
            # check that ClusterId is present, and if not add it
            format_list = complete_format_list(format_list, [("ClusterId", 'i')])
        return QueryExe.fetch(self, constraint=constraint, format_list=format_list)


# condor_status
class CondorStatus(QueryExe):
    def __init__(self,subsystem_name=None,pool_name=None,security_obj=None):
        if subsystem_name is None:
            subsystem_str=""
        else:
            subsystem_str = "-%s" % subsystem_name

        QueryExe.__init__(self,"condor_status",subsystem_str,"Name",pool_name,security_obj,{})

    def fetch(self, constraint=None, format_list=None):
        if format_list is not None:
            # check that Name present and if not, add it
            format_list = complete_format_list(format_list, [("Name",'s')])
        return QueryExe.fetch(self, constraint=constraint, format_list=format_list)


    def __repr__(self):
        output = "CondorStatus:\n"
        output += "exe_name = %s\n" % str(self.exe_name)
        output += "env = %s\n" % str(self.env)
        output += "resource_str = %s\n" % str(self.resource_str)
        output += "group_attribute = %s\n" % str(self.group_attribute)
        output += "pool_str = %s\n" % str(self.pool_str)
        output += "security_obj = %s\n" % str(self.security_obj)
        output += "stored_data = %s" % str(self.stored_data)
        return output


#
# Subquery classes
#

# Generic, you most probably don't want to use this
class BaseSubQuery(StoredQuery):
    def __init__(self, query, subquery_func):
        self.query = query
        self.subquery_func = subquery_func

    def fetch(self, constraint=None):
        indata = self.query.fetch(constraint)
        return self.subquery_func(self, indata)


    #
    # NOTE: You need to call load on the SubQuery object to use fetchStored
    #       and had query.load issued before
    #
    def load(self, constraint=None):
        indata = self.query.fetchStored(constraint)
        self.stored_data = self.subquery_func(indata)

#
# Fully usable subquery functions
#

class SubQuery(BaseSubQuery):
    def __init__(self, query, constraint_func=None):
        BaseSubQuery.__init__(self, query, lambda d:applyConstraint(d, constraint_func))

    def __repr__(self):
        output = "SubQuery:\n"
        #output += "client_name = %s\n" % str(self.client_name)
        #output += "entry_name = %s\n" % str(self.entry_name)
        #output += "factory_name = %s\n" % str(self.factory_name)
        #output += "glidein_name = %s\n" % str(self.glidein_name)
        #output += "schedd_name = %s\n" % str(self.schedd_name)
        output += "stored_data = %s" % str(self.stored_data)
        return output


class Group(BaseSubQuery):
    #  group_key_func  - Key extraction function
    #                      One argument: classad dictionary
    #                      Returns: value of the group key
    #  group_data_func - Key extraction function
    #                      One argument: list of classad dictionaries
    #                      Returns: a summary classad dictionary
    def __init__(self, query, group_key_func, group_data_func):
        BaseSubQuery.__init__(self, query, lambda d:doGroup(d, group_key_func, group_data_func))


#
# Summarizing classes
#

class Summarize:
    #  hash_func   - Hashing function
    #                One argument: classad dictionary
    #                Returns: hash value
    #                          if None, will not be counted
    #                          if a list, all elements will be used
    def __init__(self, query, hash_func=lambda x:1):
        self.query = query
        self.hash_func = hash_func

    # Parameters:
    #    constraint - string to be passed to query.fetch()
    #    hash_func  - if !=None, use this instead of the main one
    # Returns a dictionary of hash values
    #    Elements are counts (or more dictionaries if hash returns lists)
    def count(self, constraint=None, hash_func=None):
        data = self.query.fetch(constraint)
        return fetch2count(data, self.getHash(hash_func))

    # Use data pre-stored in query
    # Same output as count
    def countStored(self, constraint_func=None, hash_func=None):
        data = self.query.fetchStored(constraint_func)
        return fetch2count(data, self.getHash(hash_func))

    # Parameters, same as count
    # Returns a dictionary of hash values
    #    Elements are lists of keys (or more dictionaries if hash returns lists)
    def list(self, constraint=None, hash_func=None):
        data = self.query.fetch(constraint)
        return fetch2list(data, self.getHash(hash_func))

    # Use data pre-stored in query
    # Same output as list
    def listStored(self,constraint_func=None,hash_func=None):
        data=self.query.fetchStored(constraint_func)
        return fetch2list(data,self.getHash(hash_func))

    ### Internal
    def getHash(self, hash_func):
        if hash_func is None:
            return self.hash_func
        else:
            return hash_func

class SummarizeMulti:
    def __init__(self, queries, hash_func=lambda x:1):
        self.counts = []
        for query in queries:
            self.counts.append(self.count(query,hash_func))
        self.hash_func=hash_func

    # see Count for description
    def count(self, constraint=None, hash_func=None):
        out = {}
        
        for c in self.counts:
            data = c.count(constraint, hash_func)
            addDict(out, data)

        return out

    # see Count for description
    def countStored(self, constraint_func=None, hash_func=None):
        out = {}
        
        for c in self.counts:
            data = c.countStored(constraint_func, hash_func)
            addDict(out, data)

        return out

        


############################################################
#
# P R I V A T E, do not use
#
############################################################

# check that req_format_els are present in in_format_list, and if not add them
# return a new format_list
def complete_format_list(in_format_list, req_format_els):
    out_format_list = in_format_list[0:]
    for req_format_el in req_format_els:
        found = False
        for format_el in in_format_list:
            if format_el[0] == req_format_el[0]:
                found = True
                break
        if not found:
            out_format_list.append(req_format_el)
    return out_format_list

#
# Convert Condor XML to list
#
# For Example:
#
#<?xml version="1.0"?>
#<!DOCTYPE classads SYSTEM "classads.dtd">
#<classads>
#<c>
#    <a n="MyType"><s>Job</s></a>
#    <a n="TargetType"><s>Machine</s></a>
#    <a n="AutoClusterId"><i>0</i></a>
#    <a n="ExitBySignal"><b v="f"/></a>
#    <a n="TransferOutputRemaps"><un/></a>
#    <a n="WhenToTransferOutput"><s>ON_EXIT</s></a>
#</c>
#<c>
#    <a n="MyType"><s>Job</s></a>
#    <a n="TargetType"><s>Machine</s></a>
#    <a n="AutoClusterId"><i>0</i></a>
#    <a n="OnExitRemove"><b v="t"/></a>
#    <a n="x509userproxysubject"><s>/DC=gov/DC=fnal/O=Fermilab/OU=People/CN=Igor Sfiligoi/UID=sfiligoi</s></a>
#</c>
#</classads>
#

# 3 xml2list XML handler functions
def xml2list_start_element(name, attrs):
    global xml2list_data, xml2list_inclassad, xml2list_inattr, xml2list_intype
    if name == "c":
        xml2list_inclassad = {}
    elif name == "a":
        xml2list_inattr = {"name": attrs["n"], "val": ""}
        xml2list_intype = "s"
    elif name == "i":
        xml2list_intype = "i"
    elif name == "r":
        xml2list_intype = "r"
    elif name == "b":
        xml2list_intype = "b"
        if attrs.has_key('v'):
            xml2list_inattr["val"] = (attrs["v"] in ('T', 't', '1'))
        else:
            # extended syntax... value in text area
            xml2list_inattr["val"] = None
    elif name == "un":
        xml2list_intype = "un"
        xml2list_inattr["val"] = None
    elif name in ("s", "e"):
        pass # nothing to do
    elif name == "classads":
        pass # top element, nothing to do
    else:
        raise TypeError, "Unsupported type: %s" % name
        
def xml2list_end_element(name):
    global xml2list_data, xml2list_inclassad, xml2list_inattr, xml2list_intype
    if name == "c":
        xml2list_data.append(xml2list_inclassad)
        xml2list_inclassad = None
    elif name == "a":
        xml2list_inclassad[xml2list_inattr["name"]] = xml2list_inattr["val"]
        xml2list_inattr = None
    elif name in ("i", "b", "un", "r"):
        xml2list_intype = "s"
    elif name in ("s", "e"):
        pass # nothing to do
    elif name == "classads":
        pass # top element, nothing to do
    else:
        raise TypeError, "Unexpected type: %s" % name
    
def xml2list_char_data(data):
    global xml2list_data, xml2list_inclassad, xml2list_inattr, xml2list_intype
    if xml2list_inattr is None:
        # only process when in attribute
        return

    if xml2list_intype == "i":
        xml2list_inattr["val"] = int(data)
    elif xml2list_intype == "r":
        xml2list_inattr["val"] = float(data)
    elif xml2list_intype == "b":
        if xml2list_inattr["val"] is not None:
            #nothing to do, value was in attribute
            pass
        else:
            xml2list_inattr["val"] = (data[0] in ('T', 't', '1'))
    elif xml2list_intype == "un":
        #nothing to do, value was in attribute
        pass
    else:
        unescaped_data = string.replace(data, '\\"', '"')
        xml2list_inattr["val"] += unescaped_data

def xml2list(xml_data):
    global xml2list_data, xml2list_inclassad, xml2list_inattr, xml2list_intype

    xml2list_data = []
    xml2list_inclassad = None
    xml2list_inattr = None
    xml2list_intype = None
    
    p = xml.parsers.expat.ParserCreate()
    p.StartElementHandler = xml2list_start_element
    p.EndElementHandler = xml2list_end_element
    p.CharacterDataHandler = xml2list_char_data

    found_xml = -1
    for line in range(len(xml_data)):
        # look for the xml header
        if xml_data[line][:5] == "<?xml":
            found_xml = line
            break

    if found_xml >= 0:
        try:
            p.Parse(string.join(xml_data[found_xml:]), 1)
        except TypeError, e:
            raise RuntimeError, "Failed to parse XML data, TypeError: %s" % e
        except:
            raise RuntimeError, "Failed to parse XML data, generic error"
    # else no xml, so return an empty list
    
    return xml2list_data

#
# Convert a list to a dictionary
#
def list2dict(list_data, attr_name):
    if type(attr_name) in (type([]), type((1, 2))):
        attr_list = attr_name
    else:
        attr_list = [attr_name]
    
    dict_data = {}
    for list_el in list_data:
        if type(attr_name) in (type([]), type((1, 2))):
            dict_name = []
            list_keys=list_el.keys()
            for an in attr_name:
                if an in list_keys:
                    dict_name.append(list_el[an])
                else:
                    # Try lower cases
                    for k in list_keys:
                        if an.lower()==k.lower():
                            dict_name.append(list_el[k])
                            break
            dict_name=tuple(dict_name)
        else:
            dict_name = list_el[attr_name]
        # dict_el will have all the elements but those in attr_list
        dict_el = {}
        for a in list_el:
            if not (a in attr_list):
                dict_el[a] = list_el[a]
        dict_data[dict_name] = dict_el
    return dict_data


def applyConstraint(data, constraint_func):
    if constraint_func is None:
        return data
    else:
        outdata = {}
        for key in data.keys():
            if constraint_func(data[key]):
                outdata[key] = data[key]
    return outdata


def doGroup(indata, group_key_func, group_data_func):
    gdata = {}
    for k in indata.keys():
        inel = indata[k]
        gkey = group_key_func(inel)
        if gdata.has_key(gkey):
            gdata[gkey].append(inel)
        else:
            gdata[gkey] = [inel]

    outdata = {}
    for k in gdata.keys():
        outdata[k] = group_data_func(gdata[k])
        
    return outdata

#
# Inputs
#  data        - data from a fetch()
#  hash_func   - Hashing function
#                One argument: classad dictionary
#                Returns: hash value
#                          if None, will not be counted
#                          if a list, all elements will be used
#
# Returns a dictionary of hash values
#    Elements are counts (or more dictionaries if hash returns lists)
#
def fetch2count(data, hash_func):
    count = {}
    for k in data.keys():
        el = data[k]

        hid = hash_func(el)
        if hid is None:
            # hash tells us it does not want to count this
            continue

        # cel will point to the real counter
        cel = count

        # check if it is a list
        if (type(hid) == type([])):
            # have to create structure inside count
            for h in hid[:-1]:
                if not cel.has_key(h):
                    cel[h] = {}
                cel = cel[h]
            hid = hid[-1]

        if cel.has_key(hid):
            count_el = cel[hid] + 1
        else:
            count_el = 1
        cel[hid] = count_el
            
    return count

#
# Inputs
#  data        - data from a fetch()
#  hash_func   - Hashing function
#                One argument: classad dictionary
#                Returns: hash value
#                          if None, will not be counted
#                          if a list, all elements will be used
#
# Returns a dictionary of hash values
#    Elements are lists of keys (or more dictionaries if hash returns lists)
#
def fetch2list(data, hash_func):
    return_list = {}
    for k in data.keys():
        el = data[k]

        hid = hash_func(el)
        if hid is None:
            # hash tells us it does not want to list this
            continue

        # lel will point to the real list
        lel = return_list

        # check if it is a list
        if (type(hid) == type([])):
            # have to create structure inside list
            for h in hid[:-1]:
                if not lel.has_key(h):
                    lel[h] = {}
                lel = lel[h]
            hid = hid[-1]

        if lel.has_key(hid):
            list_el = lel[hid].append[k]
        else:
            list_el = [k]
        lel[hid] = list_el
        
    return return_list

#
# Recursivelly add two dictionaries
# Do it in place, using the first one
#
def addDict(base_dict, new_dict):
    for k in new_dict.keys():
        new_el = new_dict[k]
        if not base_dict.has_key(k):
            # nothing there?, just copy
            base_dict[k] = new_el
        else:
            if type(new_el) == type({}):
                #another dictionary, recourse
                addDict(base_dict[k], new_el)
            else:
                base_dict[k] += new_el

