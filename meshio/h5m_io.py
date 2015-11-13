# -*- coding: utf-8 -*-
#
'''
I/O for h5m, cf. <https://trac.mcs.anl.gov/projects/ITAPS/wiki/MOAB/h5m>.

.. moduleauthor:: Nico Schlömer <nico.schloemer@gmail.com>
'''
from datetime import datetime
import h5py
import numpy

from meshio import __version__


def _int_to_bool_list(num):
    # From <http://stackoverflow.com/a/33608387/353337>.
    bin_string = format(num, '04b')
    return [x == '1' for x in bin_string[::-1]]


def read(filename):
    '''Reads H5M files, cf.
    https://trac.mcs.anl.gov/projects/ITAPS/wiki/MOAB/h5m.
    '''
    f = h5py.File(filename, 'r')
    dset = f['tstt']

    points = dset['nodes']['coordinates'][()]
    # read point data
    point_data = {}
    if 'tags' in dset['nodes']:
        for name, dataset in dset['nodes']['tags'].items():
            point_data[name] = dataset[()]

    # # Assert that the GLOBAL_IDs are contiguous.
    # point_gids = dset['nodes']['tags']['GLOBAL_ID'][()]
    # point_start_gid = dset['nodes']['coordinates'].attrs['start_id']
    # point_end_gid = point_start_gid + len(point_gids) - 1
    # assert all(point_gids == range(point_start_gid, point_end_gid + 1))

    # Note that the indices are off by 1 in h5m.
    if 'Tri3' in dset['elements']:
        elems = dset['elements']['Tri3']
    elif 'Tet4' in dset['elements']:
        elems = dset['elements']['Tet4']
    else:
        raise RuntimeError('Need Tri3s or Tet4s.')

    conn = elems['connectivity']
    cells = conn[()] - 1

    cell_data = {}
    if 'tags' in elems:
        for name, dataset in elems['tags'].items():
            cell_data[name] = dataset[()]

    # The `sets` in H5M are special in that they represent a segration of data
    # in the current file, particularly by a load balancer (Metis, Zoltan,
    # etc.). This segregation has no equivalent in other data types, but is
    # certainly worthwhile visualizing.
    # Hence, we will translate the sets into cell data with the prefix "set::"
    # here.
    # TODO deal with point data
    field_data = {}
    if 'sets' in dset and 'contents' in dset['sets']:
        # read sets
        sets_contents = dset['sets']['contents'][()]
        sets_list = dset['sets']['list'][()]
        sets_tags = dset['sets']['tags']

        cell_start_gid = conn.attrs['start_id']
        cell_gids = cell_start_gid + elems['tags']['GLOBAL_ID'][()]
        cell_end_gid = cell_start_gid + len(cell_gids) - 1
        assert all(cell_gids == range(cell_start_gid, cell_end_gid + 1))

        # create the sets
        for key, value in sets_tags.items():
            mod_key = 'set::' + key
            cell_data[mod_key] = numpy.empty(len(cells), dtype=int)
            end = 0
            for k, row in enumerate(sets_list):
                bits = _int_to_bool_list(row[3])
                # is_owner = bits[0]
                # is_unique = bits[1]
                # is_ordered = bits[2]
                is_range_compressed = bits[3]
                if is_range_compressed:
                    start_gids = sets_contents[end:row[0]+1:2]
                    lengths = sets_contents[end+1:row[0]+1:2]
                    for start_gid, length in zip(start_gids, lengths):
                        end_gid = start_gid + length - 1
                        if start_gid >= cell_start_gid and \
                                end_gid <= cell_end_gid:
                            i0 = start_gid - cell_start_gid
                            i1 = end_gid - cell_start_gid + 1
                            cell_data[mod_key][i0:i1] = value[k]
                        else:
                            # TODO deal with point data
                            raise RuntimeError('')
                else:
                    gids = sets_contents[end:row[0]+1]
                    cell_data[mod_key][gids - cell_start_gid] = value[k]

                end = row[0] + 1

    return points, cells, point_data, cell_data, field_data


def write(
        filename,
        points,
        cells,
        point_data=None,
        cell_data=None,
        field_data=None
        ):
    '''Writes H5M files, cf.
    https://trac.mcs.anl.gov/projects/ITAPS/wiki/MOAB/h5m.
    '''

    f = h5py.File(filename, 'w')

    tstt = f.create_group('tstt')

    # The base index for h5m is 1.
    global_id = 1

    # add nodes
    nodes = tstt.create_group('nodes')
    coords = nodes.create_dataset('coordinates', data=points)
    coords.attrs.create('start_id', global_id)
    global_id += len(points)

    # Global tags
    tstt_tags = tstt.create_group('tags')

    # add point data
    if point_data is not None:
        tags = nodes.create_group('tags')
        for key, data in point_data.items():
            if len(data.shape) == 1:
                dtype = data.dtype
                tags.create_dataset(key, data=data)
            else:
                # H5M doesn't accept n-x-k arrays as data; it wants an n-x-1
                # array with k-tuples as entries.
                n, k = data.shape
                dtype = numpy.dtype((data.dtype, (k,)))
                dset = tags.create_dataset(key, (n,), dtype=dtype)
                dset[:] = data

            # Create entry in global tags
            g = tstt_tags.create_group(key)
            g['type'] = dtype
            # Add a class tag:
            # From
            # <http://lists.mcs.anl.gov/pipermail/moab-dev/2015/007104.html>:
            # ```
            # /* Was dense tag data in mesh database */
            #  define mhdf_DENSE_TYPE   2
            # /** \brief Was sparse tag data in mesh database */
            # #define mhdf_SPARSE_TYPE  1
            # /** \brief Was bit-field tag data in mesh database */
            # #define mhdf_BIT_TYPE     0
            # /** \brief Unused */
            # #define mhdf_MESH_TYPE    3
            #
            g.attrs['class'] = 2

    # add elements
    elements = tstt.create_group('elements')

    elem_dt = h5py.special_dtype(
        enum=('i', {
            'Edge': 1,
            'Tri': 2,
            'Quad': 3,
            'Polygon': 4,
            'Tet': 5,
            'Pyramid': 6,
            'Prism': 7,
            'Knife': 8,
            'Hex': 9,
            'Polyhedron': 10
            })
        )

    tstt['elemtypes'] = elem_dt

    tstt.create_dataset(
        'history',
        data=[
         __name__,
         __version__,
         str(datetime.now())
        ]
        )

    # number of nodes to h5m name, element type
    h5m_type = {
            2: {'name': 'Edge2', 'type': 1},
            3: {'name': 'Tri3', 'type': 2},
            4: {'name': 'Tet4', 'type': 5}
            }
    this_type = h5m_type[cells.shape[1]]
    elem_group = elements.create_group(this_type['name'])
    elem_group.attrs.create('element_type', this_type['type'], dtype=elem_dt)
    # h5m node indices are 1-based
    conn = elem_group.create_dataset('connectivity', data=(cells + 1))
    conn.attrs.create('start_id', global_id)
    global_id += len(cells)

    # add cell data
    if cell_data:
        tags = elem_group.create_group('tags')
        for key, value in cell_data.items():
            tags.create_dataset(key, data=value)

    # add empty set -- MOAB wants this
    sets = tstt.create_group('sets')
    sets.create_group('tags')

    # set max_id
    tstt.attrs.create('max_id', global_id, dtype='u8')

    return