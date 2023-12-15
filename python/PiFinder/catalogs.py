import logging
import time
from typing import List, Dict, DefaultDict, Optional
import numpy as np
import pandas as pd
from collections import defaultdict
from sklearn.neighbors import BallTree

import PiFinder.calc_utils as calc_utils
from PiFinder.db.db import Database
from PiFinder.db.objects_db import ObjectsDatabase
from PiFinder.db.observations_db import ObservationsDatabase
from PiFinder.composite_object import CompositeObject

# collection of all catalog-related classes

# CatalogBase : just the CompositeObjects
# Catalog: extends the CatalogBase with filtering
# CatalogIterator: iterates over the composite_objects
# CatalogFilter: can be set on catalog to filter
# CatalogBuilder: builds catalogs from the database
# CatalogTracker: keeps track of the current catalog and object
# Catalogs: holds all catalogs


class Names:
    """
    Holds all name related info
    """

    db: Database
    names: DefaultDict[int, List[str]] = {}

    def __init__(self):
        self.db = ObjectsDatabase()
        self.names = self.db.get_names()
        self._sort_names()
        logging.debug(f"Loaded {len(self.names)} names from database")

    def _sort_names(self):
        """
        sort the names according to some hierarchy
        """
        pass

    def get(self, object_id) -> List[str]:
        return self.names[object_id]


class CatalogFilter:
    """can be set on catalog to filter"""

    fast_aa = None

    def __init__(
        self,
        magnitude_filter=None,
        type_filter=None,
        altitude_filter=None,
        observed_filter=None,
    ):
        self.set_values(magnitude_filter, type_filter, altitude_filter, observed_filter)

    def set_values(
        self, magnitude_filter, type_filter, altitude_filter, observed_filter
    ):
        self.magnitude_filter = magnitude_filter
        self.type_filter = type_filter
        self.altitude_filter = altitude_filter
        self.observed_filter = observed_filter

    def calc_fast_aa(self, shared_state):
        solution = shared_state.solution()
        location = shared_state.location()
        dt = shared_state.datetime()
        if location and dt and solution:
            self.fast_aa = calc_utils.FastAltAz(
                location["lat"],
                location["lon"],
                dt,
            )
        else:
            logging.warning("Calc_fast_aa: location, dt, or solution not set")

    def apply_filter(self, obj: CompositeObject):
        # check altitude
        if self.altitude_filter != "None" and self.fast_aa:
            obj_altitude = self.fast_aa.radec_to_altaz(
                obj.ra,
                obj.dec,
                alt_only=True,
            )
            if obj_altitude < self.altitude_filter:
                return False

        # check magnitude

        # first try to get object mag to float
        try:
            obj_mag = float(obj.mag)
        except (ValueError, TypeError):
            obj_mag = 99

        if self.magnitude_filter != "None" and obj_mag >= self.magnitude_filter:
            return False

        # check type
        if self.type_filter != ["None"] and obj.obj_type not in self.type_filter:
            return False

        # check observed
        if self.observed_filter != "Any":
            return (self.observed_filter == "Yes") == obj.logged

        # object passed all the tests
        return True

    def apply(self, shared_state, objects: List[CompositeObject]):
        self.fast_aa = self.calc_fast_aa(shared_state)
        return [obj for obj in objects if self.apply_filter(obj)]


def catalog_base_id_sort(obj: CompositeObject):
    return obj.id


def catalog_base_sequence_sort(obj: CompositeObject):
    return obj.sequence


class CatalogBase:
    """Base class for Catalog, contains only the objects"""

    def __init__(
        self,
        catalog_code: str,
        max_sequence: int,
        desc: str,
        sort=catalog_base_sequence_sort,
    ):
        self.catalog_code = catalog_code
        self.max_sequence = max_sequence
        self.desc = desc
        self.sort = sort
        self.objects: List[CompositeObject] = []
        self.id_to_pos: Dict[int, int]
        self.sequence_to_pos: Dict[int, int]
        self.catalog_code: str
        self.max_sequence: int
        self.desc: str
        self.sort = sort

    def add_object(self, obj: CompositeObject):
        self._add_object(obj)
        self._sort_objects()
        self._update_id_to_pos()
        self._update_sequence_to_pos()

    def _add_object(self, obj: CompositeObject):
        self.objects.append(obj)

    def add_objects(self, objects: List[CompositeObject]):
        for obj in objects:
            self._add_object(obj)
        self._sort_objects()
        self._update_id_to_pos()
        self._update_sequence_to_pos()

    def _sort_objects(self):
        self.objects.sort(key=self.sort)

    def get_object_by_id(self, id: int) -> CompositeObject:
        return self.objects[self.id_to_pos[id]]

    def get_object_by_sequence(self, sequence: int) -> CompositeObject:
        return self.objects[self.sequence_to_pos[sequence]]

    def get_objects(self) -> List[CompositeObject]:
        print(
            f"get_objects: {self.catalog_code=}, {self.max_sequence=}, count={len(self.objects)}"
        )
        return self.objects

    def get_count(self) -> int:
        return len(self.objects)

    def _update_id_to_pos(self):
        self.id_to_pos = {obj.id: i for i, obj in enumerate(self.objects)}

    def _update_sequence_to_pos(self):
        self.sequence_to_pos = {obj.sequence: i for i, obj in enumerate(self.objects)}

    def __repr__(self):
        return f"Catalog({self.catalog_code=}, {self.max_sequence=}, count={self.get_count()})"

    def __str__(self):
        return self.__repr__()


class Catalog(CatalogBase):
    """Extends the CatalogBase with filtering"""

    catalog_filter: CatalogFilter
    filtered_objects: List[CompositeObject] = []
    last_filtered: float = 0

    def __init__(self, catalog_code: str, max_sequence: int, desc: str):
        super().__init__(catalog_code, max_sequence, desc)
        self.catalog_filter = CatalogFilter()
        self.filtered_objects = self.get_objects()

    def filter_objects(self, shared_state) -> List[CompositeObject]:
        self.filtered_objects = self.catalog_filter.apply(
            shared_state, self.get_objects()
        )
        self.last_filtered = time.time()
        return self.filtered_objects

    # move this code to the filter class?
    def get_filtered_count(self):
        return len(self.filtered_objects)

    def __repr__(self):
        super().__repr__()
        return f"{super().__repr__()} - filtered={self.get_filtered_count()})"

    def __str__(self):
        return self.__repr__()


class Catalogs:
    """Holds all catalogs"""

    catalogs: List[Catalog]
    catalog_dict: Dict[str, Catalog] = {}

    def __init__(self, catalogs: List[Catalog]):
        self.catalogs = catalogs

    def set(self, catalogs: List[Catalog]):
        self.catalogs = catalogs
        self.catalog_dict = {}

    def add(self, catalog: Catalog):
        cd = self.get_dict().keys()
        if catalog.catalog_code not in cd:
            self.catalogs.append(catalog)
            self.catalog_dict = {}
        else:
            logging.warning(f"Catalog {catalog.catalog_code} already exists")

    def get_names(self) -> List[str]:
        return list(self.get_dict().keys())

    def get_dict(self) -> Dict[str, Catalog]:
        if not self.catalog_dict:
            self.catalog_dict = {
                catalog.catalog_code: catalog for catalog in self.catalogs
            }
        return self.catalog_dict

    def __repr__(self):
        return f"Catalogs({self.catalogs=})"

    def __str__(self):
        return self.__repr__()


class CatalogBuilder:
    """
    Builds catalogs from the database
    Merges object table data and catalog_object table data
    """

    def build(self) -> Catalogs:
        db: Database = ObjectsDatabase()
        obs_db: Database = ObservationsDatabase()
        # list of dicts, one dict for each entry in the catalog_objects table
        catalog_objects: List[Dict] = [dict(row) for row in db.get_catalog_objects()]
        objects = db.get_objects()
        common_names = Names()
        catalogs_info = db.get_catalogs_dict()
        objects = {row["id"]: dict(row) for row in objects}
        composite_objects: List[CompositeObject] = self._build_composite(
            catalog_objects, objects, common_names, obs_db
        )
        # This is used for caching catalog dicts
        # to speed up repeated searches
        self.catalog_dicts = {}
        logging.debug(f"Loaded {len(composite_objects)} objects from database")
        return self._get_catalogs(composite_objects, catalogs_info)

    def _build_composite(
        self,
        catalog_objects: List[Dict],
        objects: Dict[int, Dict],
        common_names: Names,
        obs_db: ObservationsDatabase,
    ) -> List[CompositeObject]:
        composite_objects: List[CompositeObject] = []

        for catalog_obj in catalog_objects:
            object_id = catalog_obj["object_id"]

            # Merge the two dictionaries
            composite_data = objects[object_id] | catalog_obj

            # Create an instance from the merged dictionaries
            composite_instance = CompositeObject.from_dict(composite_data)
            composite_instance.logged = obs_db.check_logged(composite_instance)
            composite_instance.names = common_names.get(object_id)

            # Append to the result dictionary
            composite_objects.append(composite_instance)
        return composite_objects

    def _get_catalogs(
        self, composite_objects: List[CompositeObject], catalogs_info: Dict[str, Dict]
    ) -> Catalogs:
        # group composite_objects per catalog_code in a dictionary
        composite_dict: Dict[str, List[CompositeObject]] = {}
        for obj in composite_objects:
            composite_dict.setdefault(obj.catalog_code, []).append(obj)

        # convert dict of composite_objects into a List of Catalog
        catalog_list: List[Catalog] = []
        for catalog_code in catalogs_info.keys():
            catalog_info = catalogs_info[catalog_code]
            catalog = Catalog(
                catalog_code,
                max_sequence=catalog_info["max_sequence"],
                desc=catalog_info["desc"],
            )
            print(
                f"catalogBase {catalog_code} has {len(catalog.get_objects())} objects"
            )
            catalog.add_objects(composite_dict.get(catalog_code, []))
            print(f"catalog {catalog_code} has {len(catalog.get_objects())} objects")
            catalog_list.append(catalog)
            catalog = None
        return Catalogs(catalog_list)


# class CatalogState(Catalog):
#     """Keeps catalog data + filtered objects"""
#
#     last_filtered: float = 0
#
#     def __init__(
#         self, catalog_code, max_sequence, desc, catalog_objs: Dict[int, CompositeObject]
#     ):
#         self.observations_db = ObservationsDatabase()
#         self.name = catalog_code
#         self.common_names: Names = Names()
#         self.cobjects: Dict[int, CompositeObject] = {}
#         self.cobjects_keys_sorted: List[int] = []
#         self.filtered_objects: Dict[int, CompositeObject] = {}
#         self.filtered_objects_keys_sorted: List[int] = []
#         self.max_sequence = max_sequence
#         self.desc = desc
#         self.cobjects = catalog_objs
#         self.cobjects_keys_sorted = self._get_sorted_keys(self.cobjects)
#         self.filtered_objects = self.cobjects
#         self.filtered_objects_keys_sorted = self.cobjects_keys_sorted
#         assert (
#             self.cobjects_keys_sorted[-1] == self.max_sequence
#         ), f"{self.name} max sequence mismatch, {self.cobjects_keys_sorted[-1]} != {self.max_sequence}"
#         logging.info(f"loaded {len(self.cobjects)} objects for {self.name}")
#
#     def get_count(self):
#         return len(self.cobjects)
#
#     def get_filtered_count(self):
#         return len(self.filtered_objects)
#
#     def _get_sorted_keys(self, dictionary):
#         return sorted(dictionary.keys())
#
#     def filter(
#         self,
#         shared_state,
#         magnitude_filter,
#         type_filter,
#         altitude_filter,
#         observed_filter,
#     ):
#         """
#         Does filtering based on params
#         populates self._filtered_catalog
#         from in-memory catalogs
#         does not try to maintain current index because it has no notion of that
#         should be done in catalog.py
#         """
#         self.last_filtered = time.time()
#
#         self.filtered_objects = {}
#
#         if observed_filter != "Any":
#             # prep observations db cache
#             self.observations_db.load_observed_objects_cache()
#
#         fast_aa = None
#         if altitude_filter != "None":
#             # setup
#             solution = shared_state.solution()
#             location = shared_state.location()
#             dt = shared_state.datetime()
#             if location and dt and solution:
#                 fast_aa = calc_utils.FastAltAz(
#                     location["lat"],
#                     location["lon"],
#                     dt,
#                 )
#
#         for key, obj in self.cobjects.items():
#             # print(f"filtering {obj}")
#             include_obj = True
#
#             # try to get object mag to float
#             try:
#                 obj_mag = float(obj.mag)
#             except (ValueError, TypeError):
#                 obj_mag = 99
#
#             if magnitude_filter != "None" and obj_mag >= magnitude_filter:
#                 include_obj = False
#
#             if type_filter != ["None"] and obj.obj_type not in type_filter:
#                 include_obj = False
#
#             if fast_aa:
#                 obj_altitude = fast_aa.radec_to_altaz(
#                     obj.ra,
#                     obj.dec,
#                     alt_only=True,
#                 )
#                 if obj_altitude < altitude_filter:
#                     include_obj = False
#
#             if observed_filter != "Any":
#                 observed = self.observations_db.check_logged(obj)
#                 if observed:
#                     if observed_filter == "No":
#                         include_obj = False
#                 else:
#                     if observed_filter == "Yes":
#                         include_obj = False
#
#             if include_obj:
#                 self.filtered_objects[key] = obj
#         self.filtered_objects_keys_sorted = self._get_sorted_keys(self.filtered_objects)
#
#     def __repr__(self):
#         return f"Catalog({self.name=}, {self.max_sequence=})"
#
#     def __str__(self):
#         return self.__repr__()


class CatalogDesignator:
    """Holds the string that represents the catalog input/search field.
    Usually looks like 'NGC----' or 'M-13'"""

    def __init__(self, catalog_name, max_sequence):
        self.catalog_name = catalog_name
        self.object_number = 0
        self.width = len(str(max_sequence))
        self.field = self.get_designator()

    def set_target(self, catalog_index, number=0):
        assert len(str(number)) <= self.get_catalog_width()
        self.catalog_index = catalog_index
        self.object_number = number
        self.field = self.get_designator()

    def append_number(self, number):
        number_str = str(self.object_number) + str(number)
        if len(number_str) > self.get_catalog_width():
            number_str = number_str[1:]
        self.object_number = int(number_str)
        self.field = self.get_designator()

    def set_number(self, number):
        self.object_number = number
        self.field = self.get_designator()

    def has_number(self):
        return self.object_number > 0

    def reset_number(self):
        self.object_number = 0
        self.field = self.get_designator()

    def increment_number(self):
        self.object_number += 1
        self.field = self.get_designator()

    def decrement_number(self):
        self.object_number -= 1
        self.field = self.get_designator()

    def get_catalog_name(self):
        return self.catalog_name

    def get_catalog_width(self):
        return self.width

    def get_designator(self):
        number_str = str(self.object_number) if self.has_number() else ""
        return (
            f"{self.get_catalog_name(): >3} {number_str:->{self.get_catalog_width()}}"
        )

    def __str__(self):
        return self.field

    def __repr__(self):
        return self.field


class CatalogTracker:
    object_tracker: Dict[str, Optional[int]]
    designator_tracker: Dict[str, Optional[CatalogDesignator]]
    current: Catalog
    current_catalog_name: str

    def __init__(self, catalogs: Catalogs, shared_state, config_options):
        self.shared_state = shared_state
        self.config_options = config_options
        self.catalogs = catalogs
        self.designator_tracker = {
            c: CatalogDesignator(c, self.dict()[c].max_sequence) for c in self.names()
        }
        self.set_current_catalog(self.catalogs.get_names()[0])
        self.object_tracker = {c: None for c in self.names()}

    def names(self):
        return self.catalogs.get_names()

    def dict(self):
        return self.catalogs.get_dict()

    def add_foreign_catalog(self, catalog_name):
        """foreign objects not in our database, e.g. skysafari coords"""
        ui_state = self.shared_state.ui_state()
        print(f"adding foreign catalog {catalog_name}")
        print(f"current catalog names: {self.names()}")
        print(f"current catalog name: {self.current_catalog_name}")
        print(f"current catalog: {self.current_catalog}")
        print(f"current object: {self.get_current_object()}")
        print(f"current designator: {self.get_designator()}")
        print(f"ui state: {str(ui_state)}")
        self.catalogs.add(
            Catalog(
                "PUSH",
                1,
                "Skysafari push",
                {1: ui_state.target()},
            )
        )
        self.designator_tracker[catalog_name] = CatalogDesignator(catalog_name, 1)
        self.object_tracker[catalog_name] = None

    def set_current_catalog(self, catalog_name):
        if catalog_name not in self.dict():
            self.add_foreign_catalog(catalog_name)

        assert (
            catalog_name in self.dict().keys()
        ), f"{catalog_name} not in {self.dict()}"
        self.current_catalog = self.dict()[catalog_name]
        self.current_catalog_name = catalog_name

    def next_catalog(self, direction=1):
        current_index = self.names().index(self.current_catalog_name)
        next_index = (current_index + direction) % len(self.names())
        self.set_current_catalog(self.names()[next_index])

    def previous_catalog(self):
        self.next_catalog(-1)

    def next_object(self, direction=1, filtered=True):
        """
        direction: 1 for next, -1 for previous

        """
        objects = (
            self.current_catalog.filtered_objects
            if filtered
            else self.current_catalog.get_objects()
        )
        object_ids = [x.sequence for x in objects]
        current_key = self.object_tracker[self.current_catalog_name]
        next_key = None
        designator = self.get_designator()
        # there is no current object, so set the first object the first or last
        if current_key is None or current_key not in object_ids:
            next_index = 0 if direction == 1 else len(object_ids) - 1
            next_key = object_ids[next_index]
            designator.set_number(next_key)

        else:
            current_index = object_ids.index(current_key)
            next_index = current_index + direction
            if next_index == -1 or next_index >= len(object_ids):
                next_key = None  # hack to get around the fact that 0 is a valid key
                designator.set_number(0)  # todo use -1 in designator as well
            else:
                next_key = object_ids[next_index % len(object_ids)]
                designator.set_number(next_key)
        self.set_current_object(next_key)
        return self.get_current_object()

    def previous_object(self):
        return self.next_object(-1)

    def get_objects(self, catalogs=None, filtered=False) -> List[Dict]:
        logging.warning(
            f"WHERE IS THIS CALLED??? get_objects: {catalogs=}, {filtered=}"
        )
        catalog_list = self._select_catalogs(catalogs)
        object_values = []
        for catalog in catalog_list:
            if filtered:
                object_values.extend(catalog.filtered_objects.values())
            else:
                object_values.extend(catalog.cobjects.values())
        flattened_objects = [obj for entry in catalog_list for obj in object_values]
        return flattened_objects

    def does_filtered_have_current_object(self):
        return (
            self.object_tracker[self.current_catalog_name]
            in self.current_catalog.filtered_objects
        )

    def get_current_object(self) -> CompositeObject:
        object_key = self.object_tracker[self.current_catalog_name]
        if object_key is None:
            return None
        return self.current_catalog.get_object_by_sequence(object_key)

    def set_current_object(self, object_number: int, catalog_name: str = None):
        if catalog_name is not None:
            try:
                self.set_current_catalog(catalog_name)
            except AssertionError:
                # Requested catalog not in tracker!
                # Set to current catalog/zero
                catalog_name = self.current_catalog_name
                self.designator_tracker[catalog_name].set_number(0)
                return
        else:
            catalog_name = self.current_catalog_name
        self.object_tracker[catalog_name] = object_number
        self.designator_tracker[catalog_name].set_number(
            object_number if object_number else 0
        )

    def get_designator(self, catalog_name=None) -> CatalogDesignator:
        catalog_name = catalog_name or self.current_catalog_name
        return self.designator_tracker[catalog_name]

    def _select_catalog(self, catalog: Optional[str]) -> Catalog:
        catalog = self._get_catalog_name(catalog)
        return self.catalogs.get(catalog)

    def _select_catalogs(self, catalogs: Optional[List[str]]) -> List[Catalog]:
        catalog_list: List[Catalog] = []
        if catalogs is None:
            catalog_list = [self.current_catalog]
        else:
            catalog_list = [self.catalogs.get(key) for key in catalogs]
        return catalog_list

    def filter(self, catalogs=None):
        catalog_list: List[Catalog] = self._select_catalogs(catalogs=catalogs)
        magnitude_filter = self.config_options["Magnitude"]["value"]
        type_filter = self.config_options["Obj Types"]["value"]
        altitude_filter = self.config_options["Alt Limit"]["value"]
        observed_filter = self.config_options["Observed"]["value"]

        for catalog in catalog_list:
            catalog.catalog_filter.set_values(
                magnitude_filter,
                type_filter,
                altitude_filter,
                observed_filter,
            )
            catalog.filter_objects(self.shared_state)

    def get_closest_objects(self, ra, dec, n, catalogs: Optional[List[str]] = None):
        """
        Takes the current catalog or a list of catalogs, gets the filtered
        objects and returns the n closest objects to ra/dec
        """
        catalog_list: List[Catalog] = self._select_catalogs(catalogs=catalogs)
        catalog_list_flat = [
            obj for catalog in catalog_list for obj in catalog.filtered_objects.values()
        ]
        if len(catalog_list_flat) < n:
            n = len(catalog_list_flat)
        object_radecs = [
            [np.deg2rad(x.ra), np.deg2rad(x.dec)] for x in catalog_list_flat
        ]
        objects_bt = BallTree(object_radecs, leaf_size=4, metric="haversine")
        query = [[np.deg2rad(ra), np.deg2rad(dec)]]
        _dist, obj_ind = objects_bt.query(query, k=n)
        results = [catalog_list_flat[x] for x in obj_ind[0]]
        deduplicated = self._deduplicate(results)
        return deduplicated

    def _deduplicate(self, unfiltered_results):
        deduplicated_results = []
        seen_ids = set()

        for obj in unfiltered_results:
            if obj.object_id not in seen_ids:
                seen_ids.add(obj.object_id)
                deduplicated_results.append(obj)
            else:
                # If the object_id is already seen, we look at the catalog_code
                # and replace the existing object if the new object has a higher precedence catalog_code
                existing_obj_index = next(
                    i
                    for i, existing_obj in enumerate(deduplicated_results)
                    if existing_obj.object_id == obj.object_id
                )
                existing_obj = deduplicated_results[existing_obj_index]

                if (obj.catalog_code == "M" and existing_obj.catalog_code != "M") or (
                    obj.catalog_code == "NGC"
                    and existing_obj.catalog_code not in ["M", "NGC"]
                ):
                    deduplicated_results[existing_obj_index] = obj

        return deduplicated_results

    def __repr__(self):
        return f"CatalogTracker(Current:{self.current_catalog_name} {self.object_tracker[self.current_catalog_name]}, Designator:{self.designator_tracker})"
