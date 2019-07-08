#!/usr/bin/env python3

import os
import re
import subprocess
import logging
import json
import argparse
import wget
import gzip
import uuid

import sys

rootLogger = logging.getLogger()
rootLogger.setLevel(logging.INFO)
consoleHandler = logging.StreamHandler(sys.stdout)
consoleHandler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
rootLogger.addHandler(consoleHandler)

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))


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


class TopologicalSort(object):
    def __init__(self, dependency_map):
        self._dependency_map = dependency_map
        self._already_processed = set()

    def _get_dependencies(self, item, root=None):
        print("Item: %s" % item)

        if not root:
            root = item
            print("Root: %s" % root)

        elif root == item:
            logging.warn("circular dependency detected in '{}'".format(item))
            yield item
            #raise StopIteration()

        dependencies = self._dependency_map.get(item, [])
        print(dependencies)
        for dependency in dependencies:

            if dependency in self._already_processed:
                continue

            self._already_processed.add(dependency)

            for sub_dependency in self._get_dependencies(dependency, root=root):
                yield sub_dependency

            yield dependency

    def sort(self):
        print("sort")
        # Reduction, connect all nodes to a dummy node and re-calculate
        special_package_id = 'topological-sort-special-node'
        self._dependency_map[special_package_id] = self._dependency_map.keys()
        sorted_dependencies = self._get_dependencies(special_package_id)
        sorted_dependencies = list(sorted_dependencies)
        del self._dependency_map[special_package_id]

        # Remove "noise" dependencies (only referenced, not declared)
        sorted_dependencies = filter(lambda x: x in self._dependency_map, sorted_dependencies)
        return sorted_dependencies


def parse_package_gz(filename):
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

                package_desc[key] = val

            if package_desc:
                package_list.append(package_desc)

    return package_list


def get_repo_contents(user_config):
    index = {}
    for repo in user_config:
        for suite in repo["suites"]:
            for component in repo["components"]:
                for arch in repo["archs"]:
                    url = f"{repo['repo_url']}/dists/{repo['distro']}/{component}/binary-{arch}"

                    index.update({url: {"file": str(uuid.uuid4())}})
                    if not os.path.exists(f"Packages.gz.{suite}-{component}-{arch}"):
                        filename = wget.download(f"{url}/Packages.gz")
                        os.rename("Packages.gz", f"Packages.gz.{suite}-{component}-{arch}")

                    index[url]["index"] = parse_package_gz(f"Packages.gz.{suite}-{component}-{arch}")

                    if not os.path.exists(f"Packages.gz.{suite}-{component}-{arch}.json"):
                        with open(f"Packages.gz.{suite}-{component}-{arch}.json", 'w') as f:
                            json.dump(index[url]["index"], f, indent=4)

                    #os.remove(filename)

    return index

def get_dependencies(package, index, debian_packages, dependency_map):
    match = {}
    matches = []
    #print(index)
    for x,y in index.items():
        for z in y["index"]:
            if z["Package"] == package["name"]:
                matches.append(z)
            elif "Provides" in z:
                if package["name"] in z["Provides"]:
                    matches.append(z)
                
    #matches = [z for x,y in index.items() for z in y["index"] if z["Package"] == package["pkg"]]

    matches = [i for n, i in enumerate(matches) if i not in matches[n+1:]]
    matches = sorted(matches, key=lambda k: k["Version"])

    if "version" in package:
        if len(package["version"]) > 0:
            match = next((x for x in matches if x["Version"] == package["version"]), None)

            if match:
                print("BINGO")
        else:
            match = matches[0] if len(matches) > 0 else None
    else:
        match = matches[0] if len(matches) > 0 else None

    debian_package = Package(match)
    debian_packages[debian_package.id] = debian_package
    dependency_map[debian_package.id] = debian_package.dependencies

    for dep in debian_package.dependencies:

        get_dependencies(dep, index, debian_packages, dependency_map)

class Package(object):
    def __init__(self, package):
        self._metadata = package
        self.id = self._get('Package')
        self.dependencies = list(self._get_dependencies())

    def _get_dependencies(self):
        dependencies = self._get('Depends') + ',' + self._get('Pre-Depends')
        print(dependencies)
        dependencies = re.split(r',|\|', dependencies)
        print(dependencies)
        dependencies = map(lambda x: re.sub(r'\(.*\)|:any', '', x).strip(), dependencies)
        print(dependencies)
        dependencies = filter(lambda x: x, dependencies)
        print(dependencies)
        dependencies = set(dependencies)
        print(dependencies)
        for dependency in dependencies:
            yield dependency

    def _get(self, key):
        return self._metadata[key] if self._metadata[key] else ""


def get_dependencies_2(package, index, debian_packages):
    print(package)
    # Package {"name", "version", "version_test"}
    matches = []
    match = {}
    for x,y in index.items():
        for z in y["index"]:
            if z["Package"] == package["name"]:
                matches.append(z)
            elif "Provides" in z:
                if next((w for w in z["Provides"].split(" ") if package["name"] == w), None):
                    matches.append(z)
    #matches = [z for x,y in index.items() for z in y["index"] if z["Package"] == package["name"]]

    matches = [i for n, i in enumerate(matches) if i not in matches[n+1:]]
    matches = sorted(matches, key=lambda k: k["Version"])

    match = None

    if "version" in package:
        for x in matches:
            ver_chk = callProcess(f"dpkg --compare-versions {x['Version']} {package['version_test']} {package['version']}", 
                valid_returncodes=[0,1,2], inc_returncode=True)
            if ver_chk[0] == 0:
                print("Test Match")
                match = x
                break
        if not match and len(matches) > 0:
            match = matches[0]

        print("Package version %s" % package["version"])
    else:
        match = matches[0] if len(matches) > 0 else None

    if match:
        if match["Package"] in debian_packages.keys():
            print("Already accounted for")
            return
            #raise StopIteration()

        print(match)
        debian_packages[match["Package"]] = match
        sub_depends = ""

        if "Depends" in match:
            sub_depends += match["Depends"]

        if "Pre-Depends" in match:
            sub_depends += match["Pre-Depends"]

        if len(sub_depends) > 0:
            # sub_depends = match["Depends"] + ", " + match["Pre-Depends"]
            sub_depends = re.sub(", ", ",", sub_depends)
            sub_depends = sub_depends.split(",")
            print(f"Sub depends {sub_depends}")

            for pck in sub_depends:
                if len(pck.split(" ")) == 1:
                    sub_pck = pck
                    version_str = ""
                else:
                    sub_pck, version_str = re.sub('[()]', '', pck).split(" ", 1)
                sub_pck_dict = {
                    "name": sub_pck,
                    "version": version_str.split(" ")[1] if len(version_str.split(" ")) > 1 else version_str,
                    "version_test": version_str.split(" ")[0] if len(version_str.split(" ")) > 1 else ""
                }
                get_dependencies_2(sub_pck_dict, index, debian_packages)
        else:
            print("No Dependencies")
    else:
        print("No match %s" % package)
        raise StopIteration()

    print(f"Package {package} retraverse")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('-p', '--packages', help='JSON file containing list of packages to bootstrap')
    parser.add_argument("-r", "--repos", help="Config file for bootstrap")

    args  = parser.parse_args()
    vargs = vars(args)
    debian_packages = dict()

    with open(vargs["repos"], "r") as f:
       config = json.load(f)

    with open(vargs["packages"], "r") as f:
       packages = json.load(f)

    index = get_repo_contents(config)

    for package in packages:
        get_dependencies_2(package, index, debian_packages)

    print("\n\n\n")
    print(debian_packages)

    print(json.dumps(debian_packages, indent=4))

    for x,y in debian_packages.items():
        print(x)
    
    # sorted_dependencies = TopologicalSort(dependency_map).sort()
    # print(list(sorted_dependencies))
    # sorted_dependencies = map(lambda package_id: debian_packages[package_id].Package, sorted_dependencies)

    # print(list(sorted_dependencies))
