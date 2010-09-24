#!/usr/bin/env python

#
# Project:
#   glideinWMS
#
# File Version: 
#   $Id: manageFactoryDowntimes.py,v 1.39.2.3 2010/09/24 15:30:36 parag Exp $
#
# Description:
#  This program allows to add announced downtimes
#  as well as handle unexpected downtimes
#

import os.path
import time,string
import sys
import re
STARTUP_DIR=sys.path[0]
sys.path.append(os.path.join(STARTUP_DIR,"../lib"))
import glideFactoryConfig
import glideFactoryDowntimeLib
sys.path.append(os.path.join(STARTUP_DIR,"../creation/lib"))

def usage():
    print "Usage:"
    print "  manageFactoryDowntimes.py factory_dir ['all'|'factory'|'entries'|entry_name] [command]"
    print "where command is one of:"
    print "  add start_time end_time - Add a scheduled downtime period"
    print "  down [delay] [sec [com]]- Put the factory down now(+delay)" 
    print "  up [delay] [sec [com]]  - Get the factory back up now(+delay)"
    print "  ress [ISinfo]           - Set the up/down based on RESS status"
    print "  bdii [ISinfo]           - Set the up/down based on bdii status"
    print "  ress+bdii [ISinfo]      - Set the up/down based both on RESS and bdii status"
    print "  check [delay]           - Report if the factory is in downtime now(+delay)"
    #print "  export filename         - Export downtimes into a standardized XML file"
    print "the following commands can be used with factory turned off:"
    print "  vacuum                  - Remove all expired downtime info"
    #print "  import filename [wipe]  - Import the downtimes from a standardized XML file"
    print "where *_time is in one of the two formats:"
    print "  [[[YYYY-]MM-]DD-]HH:MM[:SS]"
    print "  unix_time"
    print "delay is of the format:"
    print "  [HHh][MMm][SS[s]]"
    print "and ISinfo is:"
    print "  'CEStatus' (others will follow in the future)"
    print "sec is a security class used to restrict a downtime to users"
    print "  of a specific security class defined in the factory xml"
    print "  If not specified, the downtime refers to all users."
    print "com can be a user comment relating to the downtime. Not used by WMS."
    print "  To use comments, you must enter a security class."
    print

# [[[YYYY-]MM-]DD-]HH:MM[:SS]
def strtxt2time(timeStr):
    deftime=time.localtime(time.time())
    year=deftime[0]
    month=deftime[1]
    day=deftime[2]
    seconds=0
    
    darr=timeStr.split('-')
    if len(darr)>1: # we have at least part of the date
        timeStr=darr[-1]
        day=long(darr[-2])
        if len(darr)>2:
            month=long(darr[-3])
            if len(darr)>3:
                month=long(darr[-4])

    tarr=timeStr.split(':')
    hours=long(tarr[0])
    minutes=long(tarr[1])
    if len(tarr)>2:
        seconds=long(tarr[2])

    outtime=time.mktime((year, month, day, hours, minutes, seconds, 0, 0, -1))
    return outtime


# [[[YYYY-]MM-]DD-]HH:MM[:SS]
# or
# unix_time
def str2time(timeStr):
    if len(timeStr.split(':',1))>1:
        # has a :, so it must be a text representation
        return strtxt2time(timeStr)
    else:
        # should be a simple number
        return long(timeStr)

#
#
def get_downtime_fd(entry_name,cmdname):
    if entry_name in ('entries','all'):
        print "%s not supported for %s"%(entry_name,cmdname)
        sys.exit(1)

    try:
        # New style has config all in the factory file
        #if entry_name=='factory':
        config=glideFactoryConfig.GlideinDescript()
        #else:
        #    config=glideFactoryConfig.JobDescript(entry_name)
    except IOError, e:
        raise RuntimeError, "Failed to load config for %s"%entry_name

    fd=glideFactoryDowntimeLib.DowntimeFile(config.data['DowntimesFile'])
    return fd

def get_downtime_fd_dict(entry_or_id,cmdname):
    out_fds={}
    if entry_or_id in ('entries','all'):
        glideinDescript=glideFactoryConfig.GlideinDescript()
        entries=string.split(glideinDescript.data['Entries'],',')
        for entry in entries:
            out_fds[entry]=get_downtime_fd(entry,cmdname)
        if entry_or_id=='all':
            out_fds['factory']=get_downtime_fd('factory',cmdname)
    else:
        out_fds[entry_or_id]=get_downtime_fd(entry_or_id,cmdname)

    return out_fds

def add(entry_name,argv):
    down_fd=get_downtime_fd(entry_name,argv[0])
    start_time=str2time(argv[1])
    end_time=str2time(argv[2])
    if len(argv)>2:
        sec_name=argv[3];
    down_fd.addPeriod(start_time=start_time,end_time=end_time,entry=entry_name,security_name=sec_name,comment="")
    return 0

# [HHh][MMm][SS[s]]
def delay2time(delayStr):
    hours=0
    minutes=0
    seconds=0
    harr=delayStr.split('h',1)
    if len(harr)==2:
        hours=long(harr[0])
        delayStr=harr[1]
    marr=delayStr.split('m',1)
    if len(marr)==2:
        minutes=long(marr[0])
        delayStr=marr[1]
    if delayStr[-1:]=='s':
        delayStr=delayStr[:-1] # remove final s if present
    if len(delayStr)>0:
        seconds=long(delayStr)
    
    return seconds+60*(minutes+60*hours)

def getargv(argv):
    when=0
    sec_name="All"
    comment=""
    if len(argv)>1:
        #Get delay and security class if it exists
        if (re.match("\d*h*\d*m*\ds?$",argv[1])):
            when=delay2time(argv[1])
            if len(argv)>2:
                sec_name=argv[2];
            if len(argv)>3:
                comment=" ".join(argv[3:]);
        else:
            #Format does not match time, must be security class
            sec_name=argv[1];
            if len(argv)>2:
                comment=" ".join(argv[2:]);
    return (when,sec_name,comment);



def down(entry_name,argv):
    down_fd=get_downtime_fd(entry_name,argv[0])
    (when,sec_name,comment)=getargv(argv);
    when+=long(time.time())
    if not down_fd.checkDowntime(entry=entry_name, security_class=sec_name, check_time=when): #only add a new line if not in downtimeat that time
        down_fd.startDowntime(start_time=when,security_class=sec_name,entry=entry_name,comment=comment)
    return 0

def up(entry_name,argv):
    down_fd=get_downtime_fd(entry_name,argv[0])
    (when,sec_name,comment)=getargv(argv);
    when+=long(time.time())
    if (down_fd.checkDowntime(entry=entry_name, security_class=sec_name, check_time=when)or (sec_name=="All")): 
        #only terminate downtime if there was an open period
        down_fd.endDowntime(end_time=when,entry=entry_name,security_class=sec_name)
    return 0

# This function replaces "check", which does not take into account
# security classes.  This function will read the downtimes file
# and parse it to determine whether the downtime is relevant to the 
# security class
def printtimes(entry_or_id,argv):
    config_els=get_downtime_fd_dict(entry_or_id,argv)
    when=0
    if len(argv)>1:
        if (re.match("\d*h*\d*m*\ds?$",argv[1])):
            when=delay2time(argv[1])
    when+=long(time.time())
    entry_keys=config_els.keys()
    entry_keys.sort()
    for entry in entry_keys:
        down_fd=config_els[entry]
        down_fd.printDowntime(entry=entry, check_time=when)

# This function is now deprecated, replaced by printtimes
# as it does not take into account that an entry can be down for
# only some security classes.
def check(entry_or_id,argv):
    config_els=get_downtime_fd_dict(entry_or_id,argv)

    when=0
    sec_name="All"
    if len(argv)>1:
        if (re.match("\d*h*\d*m*\ds?$",argv[1])):
            when=delay2time(argv[1])
            if len(argv)>2:
                sec_name=argv[2];
            else:
                #Format does not match time, must be security class
                sec_name=argv[1];

    when+=long(time.time())

    entry_keys=config_els.keys()
    entry_keys.sort()
    for entry in entry_keys:
        down_fd=config_els[entry]
        in_downtime=down_fd.checkDowntime(entry=entry, security_class=sec_name, check_time=when)
        if in_downtime:
            print "%s\tDown"%entry
        else:
            print "%s\tUp"%entry

    return 0

def vacuum(entry_or_id,argv):
    config_els=get_downtime_fd_dict(entry_or_id,argv)

    entry_keys=config_els.keys()
    entry_keys.sort()
    for entry in entry_keys:
        down_fd=config_els[entry]
        down_fd.purgeOldPeriods()

    return 0

def get_production_ress_entries(server,ref_dict_list):
    import condorMonitor

    production_entries=[]

    condor_obj=condorMonitor.CondorStatus(pool_name=server)
    condor_obj.load(constraint='(GlueCEInfoContactString=!=UNDEFINED)&&(GlueCEStateStatus=?="Production")',format_list=[])
    condor_refs=condor_obj.fetchStored().keys()
    #del condor_obj

    for el in ref_dict_list:
        ref=el['ref']
        if ref in condor_refs:
            production_entries.append(el['entry_name'])    
    
    return production_entries

def get_production_bdii_entries(server,ref_dict_list):
    import ldapMonitor

    production_entries=[]

    bdii_obj=ldapMonitor.BDIICEQuery(server)
    bdii_obj.load()
    bdii_obj.filterStatus(usable=True)
    bdii_refs=bdii_obj.fetchStored().keys()
    #del bdii_obj

    for el in ref_dict_list:
        ref=el['ref']
        if ref in bdii_refs:
            production_entries.append(el['entry_name'])    
    
    return production_entries

def infosys_based(entry_name,argv,infosys_types):
    # find out which entries I need to look at
    # gather downtime fds for them
    config_els={}
    if entry_name=='factory':
        return 0 # nothing to do... the whole factory cannot be controlled by infosys
    elif entry_name in ('entries','all'):
        # all==entries in this case, since there is nothing to do for the factory
        glideinDescript=glideFactoryConfig.GlideinDescript()
        entries=string.split(glideinDescript.data['Entries'],',')
        for entry in entries:
            config_els[entry]={}
    else:
        config_el={}
        config_els[entry_name]={}

    # load the infosys info
    import cgWDictFile
    import cgWConsts

    for entry in config_els.keys():
        infosys_fd=cgWDictFile.InfoSysDictFile(cgWConsts.get_entry_submit_dir('.',entry),cgWConsts.INFOSYS_FILE)
        infosys_fd.load()

        if len(infosys_fd.keys)==0:
            # entry not associated with any infosys, cannot be managed, ignore
            del config_els[entry]
            continue

        compatible_infosys=False
        for k in infosys_fd.keys:
            infosys_type=infosys_fd[k][0]
            if infosys_type in infosys_types:
                compatible_infosys=True
                break
        if not compatible_infosys:
            # entry not associated with a compatible infosys, cannot be managed, ignore
            del config_els[entry]
            continue
            
        config_els[entry]['infosys_fd']=infosys_fd

    if len(config_els.keys())==0:
        return 0 # nothing to do
    # all the remaining entries are handled by one of the supported infosys

    # summarize
    infosys_data={}
    for entry in config_els.keys():
        infosys_fd=config_els[entry]['infosys_fd']
        for k in infosys_fd.keys:
            infosys_type=infosys_fd[k][0]
            server=infosys_fd[k][1]
            ref=infosys_fd[k][2]
            if not infosys_data.has_key(infosys_type):
                infosys_data[infosys_type]={}
            infosys_data_type=infosys_data[infosys_type]
            if not infosys_data_type.has_key(server):
                infosys_data_type[server]=[]
            infosys_data_type[server].append({'ref':ref,'entry_name':entry})

    # get production entries
    production_entries=[]
    for infosys_type in infosys_data.keys():
        if infosys_type in infosys_types:
            infosys_data_type=infosys_data[infosys_type]
            for server in infosys_data_type.keys():
                infosys_data_server=infosys_data_type[server]
                if infosys_type=="RESS":
                    production_entries+=get_production_ress_entries(server,infosys_data_server)
                elif infosys_type=="BDII":
                    production_entries+=get_production_bdii_entries(server,infosys_data_server)
                else:
                    raise RuntimeError, "Unknown infosys type '%s'"%infosys_type # should never get here

    # Use the info to put the 
    entry_keys=config_els.keys()
    entry_keys.sort()
    for entry in entry_keys:
        if entry in production_entries:
            print "%s up"%entry
            up(entry,['up'])
        else:
            print "%s down"%entry
            down(entry,['down']) 
    
    return 0

def main(argv):
    if len(argv)<4:
        usage()
        return 1

    # get the downtime file from config
    factory_dir=argv[1]
    try:
        os.chdir(factory_dir)
    except OSError, e:
        usage()
        print "Failed to locate factory %s"%factory_dir
        print "%s"%e
        return 1

    entry_name=argv[2]
    cmd=argv[3]

    if cmd=='add':
        return add(entry_name,argv[3:])
    elif cmd=='down':
        return down(entry_name,argv[3:])
    elif cmd=='up':
        return up(entry_name,argv[3:])
    elif cmd=='check':
        return printtimes(entry_name,argv[3:])
    elif cmd=='ress':
        return infosys_based(entry_name,argv[3:],['RESS'])
    elif cmd=='bdii':
        return infosys_based(entry_name,argv[3:],['BDII'])
    elif cmd=='ress+bdii':
        return infosys_based(entry_name,argv[3:],['RESS','BDII'])
    elif cmd=='vacuum':
        return vacuum(entry_name,argv[3:])
    else:
        usage()
        print "Invalid command %s"%cmd
        return 1
    
if __name__ == '__main__':
    sys.exit(main(sys.argv))

