"""
Populate the GIP based upon the values from the OSG configuration
"""

import os
import re
import sys
import socket
import ConfigParser

from gip_sections import ce, site, pbs, condor, sge, lsf, se, subcluster, \
    cluster, cesebind, cream, slurm
from gip_common import getLogger, py23, vdtDir, get_file_list

log = getLogger("GIP")

site_sec = "Site Information"
misc_sec = "Misc Services"
pbs_sec = "PBS"
condor_sec = "Condor"
sge_sec = 'SGE'
storage_sec = 'Storage'
gip_sec = 'GIP'
dcache_sec = 'dcache'
lsf_sec = 'LSF'
cream_sec = 'CREAM'
slurm_sec = 'SLURM'
osgce_sec = 'OSGCE'

default_osg_ress_servers = \
    "https://osg-ress-1.fnal.gov:8443/ig/services/CEInfoCollector[OLD_CLASSAD]"
default_osg_bdii_servers = \
    "http://is1.grid.iu.edu:14001[RAW], http://is2.grid.iu.edu:14001[RAW]"
default_itb_ress_servers = \
    "https://osg-ress-4.fnal.gov:8443/ig/services/CEInfoCollector[OLD_CLASSAD]"
# We have a way to distinguish ITB from OSG sites so they don't need to go
# to a separate server (SOFTWARE-1406):
default_itb_bdii_servers = default_osg_bdii_servers

def cp_getInt(cp, section, option, default):
    """
    Helper function for ConfigParser objects which allows setting the default.
    Returns an integer, or the default if it can't make one.

    @param cp: ConfigParser object
    @param section: Section of the config parser to read
    @param option: Option in section to retrieve
    @param default: Default value if the section/option is not present.
    @returns: Value stored in the CP for section/option, or default if it is
        not present.
    """
    try:
        return int(str(cp_get(cp, section, option, default)).strip())
    except:
        return default

def cp_getBoolean(cp, section, option, default=True):
    """
    Helper function for ConfigParser objects which allows setting the default.

    If the cp object has a section/option of the proper name, and if that value
    has a 'y' or 't', we assume it's supposed to be true.  Otherwise, if it
    contains a 'n' or 'f', we assume it's supposed to be true.

    If neither applies - or the option doesn't exist, return the default

    @param cp: ConfigParser object
    @param section: Section of config parser to read
    @param option: Option in section to retrieve
    @param default: Default value if the section/option is not present.
    @returns: Value stored in CP for section/option, or default if it is not
        present.
    """
    val = str(cp_get(cp, section, option, default)).lower()
    if val.find('t') >= 0 or val.find('y') >= 0 or val.find('1') >= 0:
        return True
    if val.find('f') >= 0 or val.find('n') >= 0 or val.find('0') >= 0:
        return False
    return default

def cp_get(cp, section, option, default):
    """
    Helper function for ConfigParser objects which allows setting the default.

    ConfigParser objects throw an exception if one tries to access an option
    which does not exist; this catches the exception and returns the default
    value instead.

    This function is also found in gip_common, but is replicated here to avoid
    circular dependencies.

    @param cp: ConfigParser object
    @param section: Section of config parser to read
    @param option: Option in section to retrieve
    @param default: Default value if the section/option is not present.
    @returns: Value stored in CP for section/option, or default if it is not
        present.
    """
    if not isinstance(cp, ConfigParser.ConfigParser):
        log.error('BUG: NOTIFY GIP DEVELOPERS: cp_get called without a proper cp as first arg')
        raise RuntimeError('cp_get called without a proper cp as first arg')

    try:
        return cp.get(section, option)
    except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
        return default

def checkOsgConfigured(cp):
    """
    Make sure that the OSG has been configured when this is run.

    Checks for the presence of the file
      - In the config object, gip.osg_attribtues, if the attribute exists; else,
      - $VDT_LOCATION/monitoring/osg-attributes.conf

    @param cp: Site config object
    @return: True
    @raise ValueError: If the specified file does not exist.
    """

    etcDir = vdtDir(os.path.expandvars("$VDT_LOCATION/monitoring/"), '/etc/osg/')

    # Check to see if the osg-user-vo-map.txt exists and that its size is > 0
    defaultLoc = vdtDir(os.path.join(etcDir, "osg-user-vo-map.txt"), "/var/lib/osg/user-vo-map")
    osg_user_vo_map = cp_get(cp, "vo", "user_vo_map", defaultLoc)

    if not os.path.exists(osg_user_vo_map):
        raise ValueError("%s does not exist; we may be "
                         "running in an unconfigured OSG install!" % osg_user_vo_map)
    fd = open(osg_user_vo_map, "r")
    has_uncommented_lines = False;
    for line in fd.readlines():
        if len(line) > 3 and line[0] != "#":
            has_uncommented_lines = True
            break
    if not has_uncommented_lines:
        raise ValueError("%s has no uncommented lines; we may be "
                         "running in an unconfigured OSG install!" % osg_user_vo_map)

    return True

def configOsg(cp):
    """
    Given the config object from the GIP, overwrite data coming from the
    OSG configuration file.
    """
    # If override, then gip.conf overrides the config.ini settings
    try:
        override = cp.getboolean("gip", "override")
    except:
        override = False

    try:
        check_osg = cp.getboolean("gip", "check_osg")
    except:
        check_osg = True
    if check_osg and 'GIP_TESTING' not in os.environ:
        checkOsgConfigured(cp)

    # Load config.ini values
    cp2 = ConfigParser.ConfigParser()

    ini_dir = vdtDir(os.path.expandvars("$VDT_LOCATION/monitoring/config.ini"), '/etc/osg/config.d')
    loc = cp_get(cp, "gip", "osg_config", ini_dir)
    log.info("Using OSG config.ini %s." % loc)

    # if a directory is specified, then either we are an RPM install or a directory was
    # specified in gip.conf
    if os.path.isdir(loc):
        # get a list of the ini files in the directory
        file_list = get_file_list(loc)
    else:
        # must be a file
        file_list = [loc,]

    cp2.read(file_list)

    try:
        configSEs(cp2, cp)
    except SystemExit, KeyboardInterrupt:
        raise
    except Exception, e:
        print >> sys.stderr, str(e)
    try:
        configSubclusters(cp2, cp)
    except SystemExit, KeyboardInterrupt:
        raise
    except Exception, e:
        #log.exception(e)
        print >> sys.stderr, str(e)

    try:
        config_info(cp2, cp)
    except SystemExit, KeyboardInterrupt:
        raise
    except Exception, e:
        print >> sys.stderr, str(e)

    # Set the site status:
    try:
        state_info_file = '$VDT_LOCATION/MIS-CI/etc/grid-site-state-info'
        state_info_file = os.path.expandvars(state_info_file)
        state_info_file = vdtDir(state_info_file, '/etc/osg/grid-site-state-info')
        if os.path.exists(state_info_file):
            results = int(os.popen("/bin/sh -c 'source %s; echo " \
                "$grid_site_state_bit'" % state_info_file)
                .read().strip()) != 0
        else:
            results = None
        if not results:
            if not cp.has_section('condor'):
                cp.add_section('condor')
            cp.set('condor', 'Closed')
    except:
        pass

    # get all the items in the [GIP] section of the config.ini
    try:
        gip_items = cp2.items("GIP")
    except:
        gip_items = []
    gip_handled_items = []

    # The write_config helper function
    def __write_config(section2, option2, section, option): \
            #pylint: disable-msg=C0103
        """
        Helper function for config_compat; should not be called directly.

        To avoid circular dependencies, this is a copy of the __write_config in
        gip_common
        """
        # as we encounter the [GIP] items, add them to the handled list
        if (section2 == gip_sec) and (not (option2 in gip_handled_items)):
            gip_handled_items.append(option2)

        try:
            new_val = cp2.get(section2, option2)
        except:
            return
        if not cp.has_section(section):
            cp.add_section(section)
        if override and (not cp.has_option(section, option)):
            cp.set(section, option, new_val)
        elif (not override):
            cp.set(section, option, new_val)

    def __write_all_options_config(section2, section):
        try:
            for option in cp2.options(section2):
                __write_config(section2, option, section, option)
        except:
            pass

    def __write_config_value(section, option, new_val):
        if not cp.has_section(section):
            cp.add_section(section)
        if override and (not cp.has_option(section, option)):
            cp.set(section, option, new_val)
        elif (not override):
            cp.set(section, option, new_val)

    # Now, we compare the two - convert the config.ini options into gip.conf
    # options.
    # [Site Information]
    has_site_hostname = False
    if cp2.has_section(site_sec) and cp2.has_option(site_sec, "host_name"):
        has_site_hostname = True
    if not has_site_hostname:
        if not cp2.has_section(site_sec):
            cp2.add_section(site_sec)
        cp2.set(site_sec, "host_name", socket.gethostname())
    __write_config(site_sec, "host_name", ce, "name")
    __write_config(site_sec, "host_name", ce, "unique_name")
    __write_config(site_sec, "sponsor", site, "sponsor")
    __write_config(site_sec, "site_policy", site, "sitepolicy")
    __write_config(site_sec, "contact", site, "contact")
    __write_config(site_sec, "email", site, "email")
    __write_config(site_sec, "city", site, "city")
    __write_config(site_sec, "country", site, "country")
    __write_config(site_sec, "longitude", site, "longitude")
    __write_config(site_sec, "latitude", site, "latitude")
    __write_config(site_sec, "group", site, "group")

    site_name = getSiteName(cp2)
    __write_config_value(site, "name", site_name)
    __write_config_value(site, "unique_name", site_name)

    # [CREAM]
    if cp2.has_section(cream_sec) and cp2.has_option(cream_sec, 'enabled'):
        __write_config(cream_sec, 'enabled', cream, 'enabled')

    # [OSGCE]
    if cp2.has_section(osgce_sec) and cp2.has_option(osgce_sec, 'enabled'):
        __write_config(osgce_sec, 'enabled', osgce, 'enabled')

    # [Misc Services]
    glexec_enabled = False
    if cp2.has_section(misc_sec) and cp2.has_option(misc_sec, 'glexec_location'):
        gLexecLocation = cp2.get(misc_sec, 'glexec_location')
        if gLexecLocation.upper() != 'UNAVAILABLE':
            glexec_enabled = True
    cp.set(site, 'glexec_enabled', str(glexec_enabled))

    # WLCG-specific items
    __write_config(site_sec, "wlcg_tier", site, "wlcg_tier")
    __write_config(site_sec, "wlcg_parent", site, "wlcg_parent")
    __write_config(site_sec, "wlcg_name", site, "wlcg_name")

    # [PBS]
    __write_config(pbs_sec, "pbs_location", pbs, "pbs_path")
    # With the call to __write_all_options_config, the next 2 lines are obsolete
    #__write_config(pbs_sec, "wsgram", pbs, "wsgram")
    #__write_config(pbs_sec, "enabled", pbs, "enabled")
    # Changing the provider to use job_contact, rather than contact_string
    #__write_config(pbs_sec, "job_contact", pbs, "contact_string")

    # attempt to put all the pbs options into the internal config object
    # so that whitelisting and blacklisting work without having to resort to
    # gip.conf
    __write_all_options_config(pbs_sec, pbs)

    # [Condor]
    __write_all_options_config(condor_sec, condor)

    # [SGE]
    __write_config(sge_sec, "sge_location", sge, "sge_path")
    __write_config(sge_sec, "sge_location", sge, "sge_root")
    __write_all_options_config(sge_sec, sge)

    # [LSF]
    __write_all_options_config(lsf_sec, lsf)

    # [Slurm]
    __write_all_options_config(slurm_sec, slurm)

    # [Storage]
    __write_config(storage_sec, "app_dir", "osg_dirs", "app")
    __write_config(storage_sec, "data_dir", "osg_dirs", "data")
    __write_config(storage_sec, "grid_dir", "osg_dirs", "grid_dir")
    __write_config(storage_sec, "worker_node_temp", "osg_dirs", "wn_tmp")
    __write_config(storage_sec, "default_se", "se", "default_se")

    # [GIP]
    __write_config(gip_sec, "se_name", se, "name")
    __write_config(gip_sec, "se_host", se, "unique_name")
    __write_config(gip_sec, "se_host", se, "srm_host")
    __write_config(gip_sec, "srm_implementation", se, "implementation")
    __write_config(gip_sec, "dynamic_dcache", se, "dynamic_dcache")
    __write_config(gip_sec, "srm", se, "srm_present")
    __write_config(gip_sec, "advertise_gums", site, "advertise_gums")
    __write_config(gip_sec, "other_ces", cluster, "other_ces")
    __write_config(gip_sec, "bdii_endpoints", "gip", "bdii_endpoints")
    __write_config(gip_sec, "ress_endpoints", "gip", "ress_endpoints")

    cluster_name = cp_get(cp2, gip_sec, "cluster_name", "")
    if len(cluster_name) > 0:
        __write_config(gip_sec, "cluster_name", cluster, "name")
        cp2.set(gip_sec, "simple_cluster", "False")
        __write_config(gip_sec, "simple_cluster", cluster, "simple")

    se_bind = cp_get(cp2, gip_sec, "se_list", "")
    if len(se_bind) > 0:
        __write_config(gip_sec, "se_list", cesebind, "se_list")
        cp2.set(gip_sec, "simple_cesebind", "False")
        __write_config(gip_sec, "simple_cesebind", cesebind, "simple")

    try:
        se_only = cp2.get(gip_sec, "se_only")
    except:
        if gip_sec not in cp2.sections():
            cp2.add_section(gip_sec)
        cp2.set(gip_sec, "se_only", "True")
    
    # Try to auto-detect the batch manager.  If all are disabled or missing, 
    # then this is probably an SE only installation
    mappings = {'Condor': 'condor', 'PBS': 'pbs', 'LSF': 'lsf', 'SGE': 'sge', 'SLURM': 'slurm'}
    for section, gip_name in mappings.items():
        if cp_getBoolean(cp2, section, 'enabled', False):
            if ce not in cp.sections():
                cp.add_section(ce)
            cp.set(ce, "job_manager", gip_name)
            cp2.set(gip_sec, "se_only", "False")
    __write_config(gip_sec, "se_only", "gip", "se_only")

    __write_config(gip_sec, "batch", ce, "job_manager")

    # Storage stuff
    __write_config(gip_sec, "se_control_version", se, "srm_version")
    # Force version string of 2.2.0 or 1.1.0
    if "se" in cp.sections() and "srm_version" in cp.options("se") and \
            cp.get("se", "srm_version").find("2") >= 0:
        cp.set("se", "srm_version", "2.2.0")
    if "se" in cp.sections() and "srm_version" in cp.options("se") and \
            cp.get("se", "srm_version").find("1") >= 0:
        cp.set("se", "srm_version", "1.1.0")
    __write_config(gip_sec, "srm_version", se, "version")
    __write_config(gip_sec, "advertise_gsiftp", "classic_se", "advertise_se")
    __write_config(gip_sec, "gsiftp_host", "classic_se", "host")

    # Calculate the default path for each VO
    root_path = cp_get(cp2, gip_sec, "se_root_path", "/")
    vo_dir = cp_get(cp2, gip_sec, "vo_dir", "VONAME").replace("VONAME", "$VO")
    default = os.path.join(root_path, vo_dir)
    if not gip_sec in cp2.sections():
        cp2.add_section(gip_sec)
    cp2.set(gip_sec, "default_path", default)
    __write_config(gip_sec, "default_path", "vo", "default")

    # Override the default path with any specific paths.
    vo_dirs = cp_get(cp, gip_sec, 'special_vo_dir', 'UNAVAILABLE')
    if vo_dirs.lower().strip() != "unavailable":
        for path in vo_dirs.split(';'):
            try:
                vo, mydir = path.split(',')
            except:
                continue
            cp2.set(gip_sec, "%s_path" % vo, mydir)
            __write_config(gip_sec, "%s_path" % vo, "vo", vo)

    # Handle the subclusters.
    sc_naming = {\
        "outbound": "outbound_network",
        "name":     "name",
        "numlcpus": "cores_per_node",
        "nodes":    "node_count",
        "numpcpus":  "cpus_per_node",
        "clock":    "cpu_speed_mhz",
        "ramsize":  "ram_size",
        "vendor":   "cpu_vendor",
        "model":    "cpu_model",
        "inbound":  "inbound_network",
        "platform": "cpu_platform",
    }
    sc_number = cp_getInt(cp2, gip_sec, "sc_number", "0")
    if sc_number == 0:
        sc_number = 0
        for option in cp2.options(gip_sec):
            if option.startswith("sc_name"):
                sc_number += 1
    for idx in range(1, sc_number+1):
        sec = "subcluster_%i" % idx
        for key, val in sc_naming.items():
            __write_config(gip_sec, "sc_%s_%i" % (key, idx), sec, val)
        if not cp.has_section(sec):
            continue

        name = cp.get(sec, 'name')
        cp.set(sec, 'unique_name', name + "-" + site_name)

        nodes = cp_getInt(cp, sec, "node_count", "0")
        cpus_per_node = cp_getInt(cp, sec, "cpus_per_node", 2)
        cores_per_node = cp_getInt(cp, sec, "cores_per_node", cpus_per_node*2)
        cp.set(sec, "cores_per_cpu", "%i" % (cores_per_node/cpus_per_node))
        cp.set(sec, "total_cores", "%i" % (nodes*cores_per_node))
        cp.set(sec, "total_cpus", "%i" % (nodes*cpus_per_node))

    cp2.set(gip_sec, "bdii", "ldap://is.grid.iu.edu:2170")
    cp2.set(gip_sec, "tmp_var", "True")
    __write_config(gip_sec, "bdii", "bdii", "endpoint")
    if cp_get(cp, cluster, "simple", None) == None:
        __write_config(gip_sec, "tmp_var", "cluster", "simple")
    if cp_get(cp, "cesebind", "simple", None) == None:
        __write_config(gip_sec, "tmp_var", "cesebind", "simple")

    # add all [GIP] items that have not already been handled to the config object
    for item in gip_items:
        if not item[0] in gip_handled_items:
            __write_config(gip_sec, item[0], gip_sec.lower(), item[0])

def configSubclusters(cp, cp2):
    """
    Configure the subclusters using the new version of the subclusters config.

    Looks for the following attributes (* indicates required option):
      - name *
      - cores_per_node *
      - node_count *
      - cpus_per_node *
      - cpu_speed_mhz *
      - ram_mb *
      - cpu_vendor *
      - cpu_model *
      - inbound_network *
      - outbound_network *
      - swap_mb
      - SI00
      - SF00

    Looks for the above attributes in any section starting with the prefix
    "subcluster"
    """
    translation = { \
        "swap_mb": "swap_size",
        "ram_mb":  "ram_size",
        "cpu_platform": "platform",
    }
    siteName = getSiteName(cp)

    for section in cp.sections():
        my_sect = section.lower()
        if not my_sect.startswith(subcluster):
            continue
        try:
            cp2.add_section(my_sect)
        except ConfigParser.DuplicateSectionError:
            pass
        for option in cp.options(section):
            gip_option = translation.get(option, option)
            cp2.set(my_sect, gip_option, cp.get(section, option))
        options = cp2.options(my_sect)

        if 'name' in options:
            try:
                name = cp2.get(my_sect, 'name')
                cp2.set(my_sect, 'unique_name', name+"-"+siteName)
            except SystemExit, KeyboardInterrupt:
                raise
            except Exception, e:
                log.exception(e)

        if 'node_count' in options and 'cpus_per_node':
            try:
                cp2.set(my_sect, "total_cpus", str(int(float(cp2.get(my_sect,
                    'node_count'))*float(cp2.get(my_sect, 'cpus_per_node')))))
            except SystemExit, KeyboardInterrupt:
                raise
            except Exception, e:
                pass
        if 'cores_per_node' in options and 'cpus_per_node':
            try:
                cp2.set(my_sect, "cores_per_cpu", str(int(float(cp2.get(my_sect,
                    'cores_per_node'))/float(cp2.get(my_sect,
                    'cpus_per_node')))))
            except SystemExit, KeyboardInterrupt:
                raise
            except Exception, e:
                pass
        if 'node_count' in options and 'cores_per_node':
            try:
                cp2.set(my_sect, "total_cores", str(int(float(cp2.get(my_sect,
                    'node_count'))*float(cp2.get(my_sect, 'cores_per_node')))))
            except SystemExit, KeyboardInterrupt:
                raise
            except Exception, e:
                pass




url_re = re.compile('([A-Za-z]+)://([A-Za-z0-9-\.]+):([0-9]+)/(.+)')
split_re = re.compile("\s*,?\s*")
def configSEs(cp, cp2):
    """
    Configure all of the SEs listed in the config file.

    Looks for the following attributes (* indicates required option):
      - name *
      - unique_name
      - srm_endpoint *
      - srm_version
      - transfer_endpoints
      - provider_implementation *
      - implementation *
      - version *
      - default_path *
      - vo_paths

    Looks for the above attributes in any section starting with the prefix
    "se"
    """

    if not py23:
        # python 2.2's ConfigParser class doesn't have items().
        def cpitems(cp, section):
            d = cp.defaults().copy()
            try:
                d.update(cp._ConfigParser__sections[section])
            except KeyError:
                if section != ConfigParser.DEFAULTSECT:
                    raise ConfigParser.NoSectionError

            options = d.keys()
            if "__name__" in options:
                options.remove("__name__")

            return [(option, cp._interpolate(section, option, d[option], d))
                    for option in options]

    for section in cp.sections():
        if not section.startswith("se") and not section.startswith("SE"):
            continue
        enabled = cp_getBoolean(cp, section, "enabled")
        if enabled:
            name = cp_get(cp, section, "name", "UNKNOWN")
            my_sect = "se_%s" % name
            try:
                cp2.add_section(my_sect)
            except ConfigParser.DuplicateSectionError:
                pass
            cp2.set(my_sect, "name", name)
            # Copy over entire section
            if py23:
                for name, value in cp.items(section):
                    cp2.set(my_sect, name, value)
            else:
                for name, value in cpitems(cp, section):
                    cp2.set(my_sect, name, value)
            endpoint = cp_get(cp, section, "srm_endpoint",
                "httpg://UNKNOWN.example.com:8443/srm/v2/server")
            m = url_re.match(endpoint)
            if not m:
                print >> sys.stderr, "Invalid endpoint: %s" % endpoint
                continue
            format, host, port, endpoint = m.groups()
            cp2.set(my_sect, "srm_host", host)
            cp2.set(my_sect, "srm_port", port)
            cp2.set(my_sect, "unique_name", host)
            cp2.set(my_sect, "srm_endpoint", "httpg://%s:%s/%s" % (host, port,
                endpoint))
            cp2.set(my_sect, "srm_version", cp_get(cp, section, "srm_version", "2"))
            cp2.set(my_sect, "implementation", cp_get(cp, section, "implementation",
                "UNKNOWN"))
            cp2.set(my_sect, "version", cp_get(cp, section, "version", "UNKNOWN"))
            cp2.set(my_sect, "provider_implementation", cp_get(cp, section,
                "provider_implementation", "static"))
            cp2.set(my_sect, "infoProviderEndpoint", cp_get(cp, section,
                "infoprovider_endpoint", "file:///dev/null"))
            if cp_getBoolean(cp, gip_sec, "srm_present", False):
                cp2.set(my_sect, "srm_present", "True")
            vo_paths = split_re.split(cp_get(cp, section, "vo_paths", ""))
            for voinfo in vo_paths:
                try:
                    vo, path = voinfo.split(':')
                except:
                    continue
                vo, path = vo.strip(), path.strip()
                cp2.set(my_sect, vo, path)

            # Handle allowed VO's for dCache
            spaces_re = re.compile("space_.+_vos")
            if cp_get(cp, section, "implementation", "UNKNOWN").lower() == \
                    "dcache":
                if not cp2.has_section(dcache_sec):
                    cp2.add_section(dcache_sec)
                spaces = split_re.split(cp_get(cp, section, "spaces", ""))
                for option in cp.options(section):
                    is_space = spaces_re.match(option)
                    if not is_space:
                        continue
                    allowed_vos = cp_get(cp, section, option, "")
                    if len(allowed_vos) > 0:
                        cp2.set(dcache_sec, option, allowed_vos)
                # Add in the allowed_vos option and value so that the 
                # space_calculator module can see them if set
                allowed_vos = cp_get(cp, section, "allowed_vos", "")
                if len(allowed_vos) > 0:
                    cp2.set(dcache_sec, "allowed_vos", allowed_vos)
                
            # Handle allowed VO's for bestman
            # Yet to be implemented

info_re = re.compile("(https?)://([a-zA-Z0-9\-.]+):([0-9]+)(.*)\[(.*)\]")
def config_info(ocp, gcp):
    """
    Configure the information services.  Right now, this means that we look at
    and configure the CEMon or Info Services section from config.ini to
    determine the BDII and ReSS endpoints.  We then save this to the [GIP]
    configuration section in the bdii_endpoints and ress_endpoints attributes.

    If all else fails, we default to the OSG servers
    """

    log.debug("Starting to configure information service endpoints")

    is_osg = True
    if cp_get(ocp, "Site Information", "group", "OSG").lower().find("itb") >= 0:
        is_osg = False
    try:
        override = gcp.getboolean("gip", "override")
    except:
        override = False


    ress_endpoints = []
    bdii_endpoints = []

    # In OSG 3.1, the osg config section is [Cemon]; in 3.2 it's [Info Services]
    # since we are replacing CEMon with OSG-Info-Services.
    info_section = "Cemon"
    if ocp.has_section("Info Services"):
        info_section = "Info Services"

    # Parse the production and testing endpoints
    def parse_endpoints(name_str):
        names = split_re.split(name_str)
        results = []
        for name in names:
            m = info_re.match(name)
            if m:
                result = '%s://%s:%s%s' % m.groups()[:4]
                results.append(result)
        return results
    def get_endpoints(cp, name, default):
        name_str = cp_get(cp, info_section, name, None)
        if not name_str:
            name_str = default
        return parse_endpoints(name_str)

    # These are the default endpoints
    osg_ress_servers = get_endpoints(ocp, "osg-ress-servers",
                                     default_osg_ress_servers)
    osg_bdii_servers = get_endpoints(ocp, "osg-bdii-servers",
                                     default_osg_bdii_servers)
    itb_ress_servers = get_endpoints(ocp, "itb-ress-servers",
                                     default_itb_ress_servers)
    itb_bdii_servers = get_endpoints(ocp, "itb-bdii-servers",
                                     default_itb_bdii_servers)

    # See if the admins set something by hand; if not, go to the correct
    # endpoint depending on the grid.
    ress_servers = cp_get(ocp, info_section, "ress_servers", "UNAVAILABLE")
    ress_servers = parse_endpoints(ress_servers)
    if not ress_servers:
        if is_osg:
            ress_servers = osg_ress_servers
        else:
            ress_servers = itb_ress_servers

    bdii_servers = cp_get(ocp, info_section, "bdii_servers", "UNAVAILABLE")
    bdii_servers = parse_endpoints(bdii_servers)
    if not bdii_servers:
        if is_osg:
            bdii_servers = osg_bdii_servers
        else:
            bdii_servers = itb_bdii_servers

    if not gcp.has_section("gip"):
        gcp.add_section("gip")

    # As appropriate, override the GIP settings.
    gip_bdii_servers = cp_get(gcp, "gip", "bdii_endpoints", None)
    if (bdii_servers and override) or (bdii_servers and not gip_bdii_servers):
        gcp.set("gip", "bdii_endpoints", ", ".join(bdii_servers))
        log.info("Configured BDII endpoints: %s." % ", ".join(bdii_servers))
    else:
        log.info("Previously configured BDII endpoints: %s." % \
            ", ".join(gip_bdii_servers))

    gip_ress_servers = cp_get(gcp, "gip", "ress_endpoints", None)
    if (ress_servers and override) or (ress_servers and not gip_ress_servers):
        gcp.set("gip", "ress_endpoints", ", ".join(ress_servers))
        log.info("Configured ReSS endpoints: %s." % ", ".join(ress_servers))
    else:
        log.info("Previously configured ReSS endpoints: %s." % \
            ", ".join(gip_ress_servers))

def getSiteName(cp):
    siteName = cp_get(cp, site_sec, "resource_group", "UNKNOWN")
    if siteName == "UNKNOWN":
        siteName = cp_get(cp, site_sec, "site_name", "UNKNOWN")
    if siteName == "UNKNOWN":
        siteName = cp_get(cp, site_sec, "resource", "UNKNOWN")
    if siteName == "UNKNOWN":
        # adding in as a last option Brian's previous name for resource group
        siteName = cp_get(cp, site_sec, "resource_group_name", "UNKNOWN")

    return siteName
