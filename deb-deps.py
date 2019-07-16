import sys
import argparse
import json
import wget
import uuid
import os
import gzip
import re
from operator import attrgetter
from collections import defaultdict
from itertools import chain
import subprocess
import getpass
import apt_pkg


def callProcess(cmd, live_output=False, printcmd=False, curdir="/", valid_returncodes=[0,], root=False, inc_returncode=False):
	try:
		output = ""

		if printcmd:
			print(cmd)

		if root and getpass.getuser() != "root":
			cmd = "sudo " + cmd

		with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, cwd=curdir) as process:
			if live_output:
				for line in iter(process.stdout.readline, ''):
					if len(line) > 0:
						print(line.decode("ascii", "ignore").rstrip("\n"))
						output = output + line.decode("ascii", "ignore")
					else:
						break

				process.communicate()
			else:
				data   = process.communicate()
				output = data[0].decode("utf-8")

			if not process.returncode in valid_returncodes:
				raise subprocess.CalledProcessError(process.returncode, cmd=cmd, output=output)

			if inc_returncode:
				return (process.returncode, output)
			else:
				return output

	except subprocess.CalledProcessError as e:
		print("Failed to call %s" % (e.cmd))
		print("Reason %s" % (e.output))

		raise

	except Exception as e:
		print("Failed PySNE.common.callProcess: %s" % str(e))

		raise


def parse_package_gz(filename, repo_url):
    package_list = []
    with gzip.open(filename, "rb") as f:
        lines = f.read().decode()

        packages = lines.split("\n\n")
        
        for p in packages:
            package_desc = {}
            items = p.split("\n")

            for i in items:
                j = i.split(":", 1)

                key = j[0] if j[0] else None
                val = j[1].lstrip() if len(j) > 1 else None

                if not key or not val:
                    continue

                if key == "Pre-Depends" or key == "Depends":
                    # print(val)
                    # Concat PreDepends and Depends
                    if len(val) > 0:
                        if "Depends" not in package_desc.keys():
                            package_desc["Depends"] = list()
                            package_desc["DependsOrig"] = ""
                        
                        package_desc["DependsOrig"] += val

                        val = re.sub(", ", ",", val).split(",")

                        # package_desc["Depends"] = list()

                        for sub in val:
                            if len(sub.split(" ")) == 1:
                                sub = sub.split(":")
                                if len(sub) == 1:
                                    sub = sub[0]
                                else:
                                    if sub[1] == "any":
                                        sub = sub[0]
                                    else:
                                        raise Exception(f"Not able to handle this yet {sub}") # Colon splitting is architecture
                                sub_pck_dict = {
                                    "name": sub, 
                                    "version": "",
                                    "version_test": ""
                                }
                            else:
                                sub_pck, version_str = re.sub('[()]', '', sub).split(" ", 1)

                                sub_pck = sub_pck.split(":")
                                if len(sub_pck) == 1:
                                    sub_pck = sub_pck[0]
                                else:
                                    if sub_pck[1] == "any":
                                        sub_pck = sub_pck[0]
                                    else:
                                        raise Exception(f"Not able to handle this yet {sub}")

                                sub_pck_dict = {
                                    "name": sub_pck,
                                    "version": version_str.split(" ")[1] if len(version_str.split(" ")) > 1 else version_str,
                                    "version_test": version_str.split(" ")[0] if len(version_str.split(" ")) > 1 else "=="
                                }

                            package_desc["Depends"].append({"key": sub_pck_dict["name"], "value": sub_pck_dict})

                else:
                    package_desc[key] = val

            if package_desc:
                package_desc["repo_url"] = repo_url
                package_list.append(package_desc)

    return package_list


def get_repo_contents(user_config):
    index = {}
    package_list = []
    for repo in user_config:
        for suite in repo["suites"]:
            for component in repo["components"]:
                for arch in repo["archs"]:
                    url = f"{repo['repo_url']}/dists/{repo['distro']}/{component}/binary-{arch}"

                    index.update({url: {"file": str(uuid.uuid4())}})
                    if not os.path.exists(f"Packages.gz.{suite}-{component}-{arch}"):
                        filename = wget.download(f"{url}/Packages.gz")
                        os.rename("Packages.gz", f"Packages.gz.{suite}-{component}-{arch}")

                    index[url]["index"] = parse_package_gz(f"Packages.gz.{suite}-{component}-{arch}", url)
                    package_list.extend(index[url]["index"])

                    if not os.path.exists(f"Packages.gz.{suite}-{component}-{arch}.json"):
                        with open(f"Packages.gz.{suite}-{component}-{arch}.json", 'w') as f:
                            json.dump(index[url]["index"], f, indent=4)

                    #os.remove(filename)

    return (index, package_list)


def build_index(pkgs):
    index = {}

    for p in pkgs:
        if not p["Package"] in index.keys():
            index[p["Package"]] = []

        index[p["Package"]].append(p)

        provides = re.sub(", ", ",", p["Provides"]).split(",") if "Provides" in p.keys() else []

        for pro in provides:
            if not pro in index.keys():
                index[pro] = []
                index[pro].append(p)

    return index


def main():
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('-p', '--packages', help='JSON file containing list of packages to bootstrap')
    parser.add_argument("-r", "--repos", help="Config file for bootstrap")

    args  = parser.parse_args()
    vargs = vars(args)
    debian_packages = dict()
    debian_packages_basic = dict()

    apt_pkg.init_system()

    with open(vargs["repos"], "r") as f:
       config = json.load(f)

    with open(vargs["packages"], "r") as f:
       packages = json.load(f)

    # Get Dict of all packages in relevant Packages.gz files

    index = get_repo_contents(config)
    with open(vargs["packages"], "r") as f:
       packages = json.load(f)

    bindex = build_index(index[1])

    deps = []

    packages = [
        {"name": "curl", "version": "", "version_test": ""},
        {"name": "bash", "version": "", "version_test": ""},
        {"name": "terminator", "version": "", "version_test": ""}
    ]

    for pkg in packages:
        build_deps(pkg, deps, bindex)

    print([x["Package"] for x in deps])

    return


class AptVerChk():
    lookup = {
        "<": lambda x: x < 0,
        "<<": lambda x: x < 0,
        "<=": lambda x: x <= 0,
        "=": lambda x: x == 0,
        ">=": lambda x: x >= 0,
        ">>": lambda x: x > 0,
        ">": lambda x: x > 0
    }

    @classmethod
    def compare(cls, a, sym, b):
        comp = apt_pkg.version_compare(a, b)

        return cls.lookup[sym](comp) if sym in cls.lookup.keys() else False


def build_deps(package, deps, index, dep_stack=[]):
    # print(f"Root Dep {package['name']}: {dep_stack}")
    # print(dep_stack)

    if package["name"] in index.keys():
        found = False
        for pkg_inst in index[package["name"]]:
            #print(pkg_inst["Package"])
            
            if len(package['version']) > 0:
                if not AptVerChk.compare(pkg_inst['Version'], package['version_test'], package['version']):
                    raise ValueError(f"Cant find version match for {package['name']}, {pkg_inst['Version']} {package['version_test']} {package['version']}")
                else:
                    found = True

            if pkg_inst["Package"] in dep_stack:
                # print("Cyclic")
                if found:
                    break
                continue
            else:
                dep_stack.append(pkg_inst["Package"])
                if pkg_inst not in deps:
                    deps.append(pkg_inst)
                else:
                    if found:
                        break
                    continue

                if "Depends" in pkg_inst.keys():
                    # print(pkg_inst["Depends"])
                    for v in pkg_inst["Depends"]:
                        build_deps(v["value"], deps, index, dep_stack)

            if found:
                break
    else:
        raise ValueError(f"Package not found {package['name']}")

    if len(dep_stack) > 0:
        dep_stack.pop()

    return

if __name__ == '__main__':
    sys.exit(main())