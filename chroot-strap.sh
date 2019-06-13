#!/bin/bash

LIB_PATHS="/usr/lib /lib"
SEED_DEPENDENCY="/bin/bash"

array=

search_deps()
{
	seed=$1
	obj=`objdump -p $seed | grep NEEDED | rev | cut -d " " -f1 | rev`

	for item in $obj; do
		echo "Dep find $item"
		# Find file
		loc=`find $LIB_PATHS -name $item`
		# Follow link if necessary
		dep=`realpath $loc`
		search_deps $dep
	done
	array+=($1)
}

search_deps /bin/bash

echo ${array[@]} | tr " " "\n"