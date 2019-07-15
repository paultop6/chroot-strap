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

try:
    from collections import OrderedDict
except ImportError:
    from ordereddict import OrderedDict

flatten = chain.from_iterable

class Package(object):
    """Abstract class for wrappers around objects that pip returns.

    This class needs to be subclassed with implementations for
    `render_as_root` and `render_as_branch` methods.

    """

    def __init__(self, obj):
        self._obj = obj
        #print(obj)
        if "Package" not in obj:
            raise StopIteration(obj)
        self.package = obj["Package"]
        self.key = obj["Package"]

    def render_as_root(self, frozen):
        return NotImplementedError

    def render_as_branch(self, frozen):
        return NotImplementedError

    def render(self, parent=None, frozen=False):
        if not parent:
            return self.render_as_root(frozen)
        else:
            return self.render_as_branch(frozen)

    # @staticmethod
    # def frozen_repr(obj):
    #     fr = frozen_req_from_dist(obj)
    #     return str(fr).strip()

    def __getattr__(self, key):
        # if key in self._obj:
        #     print(self._obj[key])
        #     print(self._obj.values())
        return self._obj[key] if key in self._obj else []

    def __repr__(self):
        return '<{0}("{1}")>'.format(self.__class__.__name__, self.key)


class DistPackage(Package):
    """Wrapper class for pkg_resources.Distribution instances

      :param obj: pkg_resources.Distribution to wrap over
      :param req: optional ReqPackage object to associate this
                  DistPackage with. This is useful for displaying the
                  tree in reverse
    """

    def __init__(self, obj, req=None):
        super(DistPackage, self).__init__(obj)
        self.version_spec = None
        self.req = req

    def render_as_root(self, frozen):
        if not frozen:
            return '{0}=={1}'.format(self.project_name, self.version)
        else:
            return self.__class__.frozen_repr(self._obj)

    def render_as_branch(self, frozen):
        assert self.req is not None
        if not frozen:
            parent_ver_spec = self.req.version_spec
            parent_str = self.req.project_name
            if parent_ver_spec:
                parent_str += parent_ver_spec
            return (
                '{0}=={1} [requires: {2}]'
            ).format(self.project_name, self.version, parent_str)
        else:
            return self.render_as_root(frozen)

    def as_requirement(self):
        """Return a ReqPackage representation of this DistPackage"""
        return ReqPackage(self._obj.as_requirement(), dist=self)

    def as_required_by(self, req):
        """Return a DistPackage instance associated to a requirement

        This association is necessary for displaying the tree in
        reverse.

        :param ReqPackage req: the requirement to associate with
        :returns: DistPackage instance

        """
        return self.__class__(self._obj, req)

    def as_dict(self):
        return {'key': self.key,
                'package_name': self.project_name,
                'installed_version': self.version}


class ReqPackage(Package):
    """Wrapper class for Requirements instance

      :param obj: The `Requirements` instance to wrap over
      :param dist: optional `pkg_resources.Distribution` instance for
                   this requirement
    """

    UNKNOWN_VERSION = '?'

    def __init__(self, obj, dist=None):
        super(ReqPackage, self).__init__(obj)
        self.dist = dist

    @property
    def version_spec(self):
        specs = sorted(self._obj.specs, reverse=True)  # `reverse` makes '>' prior to '<'
        return ','.join([''.join(sp) for sp in specs]) if specs else None

    @property
    def installed_version(self):
        if not self.dist:
            return guess_version(self.key, self.UNKNOWN_VERSION)
        return self.dist.version

    def is_conflicting(self):
        """If installed version conflicts with required version"""
        # unknown installed version is also considered conflicting
        if self.installed_version == self.UNKNOWN_VERSION:
            return True
        ver_spec = (self.version_spec if self.version_spec else '')
        req_version_str = '{0}{1}'.format(self.project_name, ver_spec)
        req_obj = pkg_resources.Requirement.parse(req_version_str)
        return self.installed_version not in req_obj

    def render_as_root(self, frozen):
        if not frozen:
            return '{0}=={1}'.format(self.project_name, self.installed_version)
        elif self.dist:
            return self.__class__.frozen_repr(self.dist._obj)
        else:
            return self.project_name

    def render_as_branch(self, frozen):
        if not frozen:
            req_ver = self.version_spec if self.version_spec else 'Any'
            return (
                '{0} [required: {1}, installed: {2}]'
                ).format(self.project_name, req_ver, self.installed_version)
        else:
            return self.render_as_root(frozen)

    def as_dict(self):
        return {'key': self.key,
                'package_name': self.project_name,
                'installed_version': self.installed_version,
                'required_version': self.version_spec}


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
                                sub_pck = sub
                                version_str = ""
                            else:
                                sub_pck, version_str = re.sub('[()]', '', sub).split(" ", 1)

                            package_desc["Depends"].append({"key": sub_pck, "value": version_str})

                else:
                    package_desc[key] = val

            # if package_desc["Package"] == "bash":
            #     print(package_desc)
            #     raise StopIteration("Bash")

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
    return dict((p["Package"], DistPackage(p)) for p in pkgs)


def construct_tree(index):
    """Construct tree representation of the pkgs from the index.

    The keys of the dict representing the tree will be objects of type
    DistPackage and the values will be list of ReqPackage objects.

    :param dict index: dist index ie. index of pkgs by their keys
    :returns: tree of pkgs and their dependencies
    :rtype: dict

    """
    tree = dict()
    
    for p in index.values():
        deps = list()
        for r in p.Depends:
            find = index.get(r["key"]) if r["key"] in index else None

            if find:
                deps.append(ReqPackage(index.get(r["key"])._obj))
        
        tree.update({p: deps})

    return tree

def conflicting_deps(tree):
    """Returns dependencies which are not present or conflict with the
    requirements of other packages.

    e.g. will warn if pkg1 requires pkg2==2.0 and pkg2==1.0 is installed

    :param tree: the requirements tree (dict)
    :returns: dict of DistPackage -> list of unsatisfied/unknown ReqPackage
    :rtype: dict

    """
    conflicting = defaultdict(list)
    for p, rs in tree.items():
        for req in rs:
            if req.is_conflicting():
                conflicting[p].append(req)
    return conflicting


def cyclic_deps(tree):
    """Return cyclic dependencies as list of tuples

    :param list pkgs: pkg_resources.Distribution instances
    :param dict pkg_index: mapping of pkgs with their respective keys
    :returns: list of tuples representing cyclic dependencies
    :rtype: generator

    """
    key_tree = dict((k.key, v) for k, v in tree.items())
    get_children = lambda n: key_tree.get(n.key, [])
    cyclic = []
    for p, rs in tree.items():
        for req in rs:
            if p.key in map(attrgetter('key'), get_children(req)):
                print((p, req, p))
                cyclic.append((p, req, p))
    return cyclic


def sorted_tree(tree):
    """Sorts the dict representation of the tree

    The root packages as well as the intermediate packages are sorted
    in the alphabetical order of the package names.

    :param dict tree: the pkg dependency tree obtained by calling
                     `construct_tree` function
    :returns: sorted tree
    :rtype: collections.OrderedDict

    """
    # ls = []
    # for k, v in tree.items():
    #     ls.append((k, sorted(v, key=attrgetter('key'))))

    # print(sorted(ls, key=lambda kv: kv[0].key))

    return OrderedDict(sorted([(k, sorted(v, key=attrgetter('key')))
                               for k, v in tree.items()],
                              key=lambda kv: kv[0].key))


def render_tree(tree, list_all=True, show_only=None, frozen=False, exclude=None):
    """Convert tree to string representation

    :param dict tree: the package tree
    :param bool list_all: whether to list all the pgks at the root
                          level or only those that are the
                          sub-dependencies
    :param set show_only: set of select packages to be shown in the
                          output. This is optional arg, default: None.
    :param bool frozen: whether or not show the names of the pkgs in
                        the output that's favourable to pip --freeze
    :param set exclude: set of select packages to be excluded from the
                          output. This is optional arg, default: None.
    :returns: string representation of the tree
    :rtype: str

    """
    tree = sorted_tree(tree)
    di = {}
    for i,j in tree.items():
        deps = ", ".join([x.key for x in j])
        d = {
            i.key: deps
        }
        di.update(d)
    print(di)

    # print(tree)
    return
    branch_keys = set(r.key for r in flatten(tree.values()))
    nodes = tree.keys()
    use_bullets = not frozen

    key_tree = dict((k.key, v) for k, v in tree.items())
    get_children = lambda n: key_tree.get(n.key, [])

    if show_only:
        nodes = [p for p in nodes
                 if p.key in show_only or p.project_name in show_only]
    elif not list_all:
        nodes = [p for p in nodes if p.key not in branch_keys]

    def aux(node, parent=None, indent=0, chain=None):
        if exclude and (node.key in exclude or node.project_name in exclude):
            return []
        if chain is None:
            chain = [node.project_name]
        node_str = node.render(parent, frozen)
        if parent:
            prefix = ' '*indent + ('- ' if use_bullets else '')
            node_str = prefix + node_str
        result = [node_str]
        children = [aux(c, node, indent=indent+2,
                        chain=chain+[c.project_name])
                    for c in get_children(node)
                    if c.project_name not in chain]
        result += list(flatten(children))
        return result

    lines = flatten([aux(p) for p in nodes])
    return '\n'.join(lines)
    

def main():
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('-p', '--packages', help='JSON file containing list of packages to bootstrap')
    parser.add_argument("-r", "--repos", help="Config file for bootstrap")

    args  = parser.parse_args()
    vargs = vars(args)
    debian_packages = dict()
    debian_packages_basic = dict()

    with open(vargs["repos"], "r") as f:
       config = json.load(f)

    with open(vargs["packages"], "r") as f:
       packages = json.load(f)

    # Get Dict of all packages in relevant Packages.gz files

    index = get_repo_contents(config)
    with open(vargs["packages"], "r") as f:
       packages = json.load(f)

    bindex = build_index(index[1])
    #print(bindex)
    #return
    tree = construct_tree(bindex)

    cyclic = cyclic_deps(tree)
    cyclic_lookup = {}

    for i in cyclic:
        d = {
            i[0].key: i[1].key
        }
        cyclic_lookup.update(d)
    # for i,j in cyclic.items():
    #     deps = ", ".join([x.key for x in j])
    #     d = {
    #         i.key: deps
    #     }
        

    tree = sorted_tree(tree)
    tree_lookup = {}

    for i,j in tree.items():
        deps = [x.key for x in j]
        d = {
            i.key: deps
        }
        tree_lookup.update(d)

    with open(f"tree.json", 'w') as f:
        json.dump(tree_lookup, f, indent=4)

    with open(f"cyclic.json", 'w') as f:
        json.dump(cyclic_lookup, f, indent=4)

    deps = []

    build_deps("curl", tree_lookup, cyclic_lookup, deps, root=True)

    print(deps)
    #print(json.dumps(di, indent=4))
    #conflict = conflicting_deps(tree)
    #print(conflict)
    #print(cyclic)
    # print("Print tree")
    # print(tree)

    #print(json.dumps(index[1], indent=4))

def build_deps(package, tree, cyclic, final_deps, root=False):
    if package in tree.keys():
        deps = tree[package]

        print(deps)

        for dep in deps:
            if root:
                print(f"Root dep {dep}")
            else:
                print(dep)
            if dep in cyclic.keys():
                print("Cyclic")

                if dep in final_deps:
                    print("Cyclic parsed")
                else:
                    print(f"Appending package {cyclic[dep]}")
                    final_deps.append(dep)
                    build_deps(dep, tree, cyclic, final_deps)
            else:
                build_deps(dep, tree, cyclic, final_deps)

        if not package in final_deps:
            final_deps.append(package)
    else:
        raise ValueError(f"Package not found {package}")

    return

if __name__ == '__main__':
    sys.exit(main())