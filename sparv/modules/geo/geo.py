"""Annotate geographical features."""

import pickle
from collections import defaultdict

import sparv.util as util
from sparv import Annotation, Document, Model, Output, annotator


@annotator("Annotate chunks with location data, based on locations contained within the text")
def contextual(doc: str = Document,
               out: str = Output("{chunk}:geo.geo", description="Geographical places with coordinates"),
               chunk: str = Annotation("{chunk}"),
               context: str = Annotation("{context}"),
               ne_type: str = Annotation("swener.ne:swener.type"),
               ne_subtype: str = Annotation("swener.ne:swener.subtype"),
               ne_name: str = Annotation("swener.ne:swener.name"),
               model: str = Model("[geo.model=geo/geo.pickle]"),
               method: str = "populous",
               language: list = []):
    """Annotate chunks with location data, based on locations contained within the text.

    context = text chunk to use for disambiguating places (when applicable).
    chunk = text chunk to which the annotation will be added.
    """
    if isinstance(language, str):
        language = language.split()

    model = load_model(model, language=language)

    ne_type_annotation = list(util.read_annotation(doc, ne_type))
    ne_subtype_annotation = list(util.read_annotation(doc, ne_subtype))
    ne_name_annotation = list(util.read_annotation(doc, ne_name))

    children_context_chunk, _orphans = util.get_children(doc, context, chunk)
    children_chunk_ne, _orphans = util.get_children(doc, chunk, ne_type)

    out_annotation = util.create_empty_attribute(doc, chunk)

    for chunks in children_context_chunk:
        all_locations = []  # TODO: Maybe not needed for anything?
        context_locations = []
        chunk_locations = defaultdict(list)

        for ch in chunks:
            for n in children_chunk_ne[ch]:
                if ne_type_annotation[n] == "LOC" and "PPL" in ne_subtype_annotation[n]:
                    location_text = ne_name_annotation[n].replace("\n", " ").replace("  ", " ")
                    location_data = model.get(location_text.lower())
                    if location_data:
                        all_locations.append((location_text, list(location_data)))
                        context_locations.append((location_text, list(location_data)))
                        chunk_locations[ch].append((location_text, list(location_data)))
                    else:
                        pass
                        # util.log.info("No location found for %s" % ne_name_annotation[n].replace("%", "%%"))

        chunk_locations = most_populous(chunk_locations)

        for c in chunks:
            out_annotation[c] = _format_location(chunk_locations.get(c, ()))

    util.write_annotation(doc, out, out_annotation)


@annotator("Annotate chunks with location data, based on metadata containing location names")
def metadata(doc: str = Document,
             out: str = Output("{chunk}:geo.geo", description="Geographical places with coordinates"),
             chunk: str = Annotation("{chunk}"),
             source: str = Annotation("{source}"),
             model: str = Model("[geo.model=geo/geo.pickle]"),
             method: str = "populous",
             language: list = []):
    """Get location data based on metadata containing location names."""
    if isinstance(language, str):
        language = language.split()

    model = load_model(model, language=language)

    same_target_source = util.split_annotation(chunk)[0] == util.split_annotation(source)[0]
    chunk_annotation = list(util.read_annotation(doc, chunk))
    source_annotation = list(util.read_annotation(doc, source))

    # If location source and target chunk are not the same, we need
    # to find the parent/child relations between them.
    if not same_target_source:
        target_source_parents = list(util.get_parents(doc, source, chunk))

    chunk_locations = {}

    for i, _ in enumerate(chunk_annotation):
        if same_target_source:
            location_source = source_annotation[i]
        else:
            location_source = source_annotation[target_source_parents[i]] if target_source_parents[i] is not None else None

        if location_source:
            location_data = model.get(location_source.strip().lower())
            if location_data:
                chunk_locations[i] = [(location_source, list(location_data))]
        else:
            chunk_locations[i] = []

    chunk_locations = most_populous(chunk_locations)

    out_annotation = util.create_empty_attribute(doc, chunk_annotation)
    for c in chunk_locations:
        out_annotation[c] = _format_location(chunk_locations.get(c, ()))

    util.write_annotation(doc, out, out_annotation)


def build_model(geonames, alternative_names, out, protocol=-1):
    """Read list of cities from Geonames dump (http://download.geonames.org/export/dump/).

    Add alternative names for each city.
    """
    util.log.info("Reading geonames: %s", geonames)
    result = {}
    with open(geonames, encoding="UTF-8") as model_file:
        for line in model_file:
            geonameid, name, _, _, latitude, longitude, _feature_class, _feature_code, \
                country, _, _admin1, _admin2, _admin3, _admin4, population, _, _, _, _ = line.split("\t")

            result[geonameid] = {
                "name": name,
                "alternative_names": {},
                "latitude": latitude,
                "longitude": longitude,
                "country": country,
                "population": population
            }

    # Parse file with alternative names of locations, paired with language codes
    util.log.info("Reading alternative names: %s", alternative_names)

    with open(alternative_names, encoding="UTF-8") as model_file:
        for line in model_file:
            _altid, geonameid, isolanguage, altname, _is_preferred_name, _is_short_name, \
                _is_colloquial, _is_historic = line.split("\t")
            if geonameid in result:
                result[geonameid]["alternative_names"].setdefault(isolanguage, [])
                result[geonameid]["alternative_names"][isolanguage].append(altname)

    util.log.info("Saving geomodel in Pickle format")

    with open(out, "wb") as outfile:
        pickle.dump(result, outfile, protocol=protocol)

    util.log.info("OK, saved")


########################################################################################################
# HELPERS
########################################################################################################


def load_model(model, language=()):
    """Load geo model and return as dict."""
    util.log.info("Reading geomodel: %s", model)
    with open(model, "rb") as infile:
        m = pickle.load(infile)

    result = defaultdict(set)
    for _geonameid, l in list(m.items()):
        result[l["name"].lower()].add((l["name"], l["latitude"], l["longitude"], l["country"], l["population"]))
        for lang in l["alternative_names"]:
            if lang in language or not language:
                for altname in l["alternative_names"][lang]:
                    result[altname.lower()].add((l["name"], l["latitude"], l["longitude"], l["country"], l["population"]))

    util.log.info("Read %d geographical names", len(result))

    return result


def most_populous(locations):
    """Disambiguate locations by only keeping the most populous ones."""
    new_locations = {}

    for chunk in locations:
        new_locations[chunk] = set()

        for l in locations[chunk]:
            biggest = (l[0], sorted(l[1], key=lambda x: -int(x[-1]))[0])
            new_locations[chunk].add(biggest)
    return new_locations


def _format_location(location_data):
    """Format location as city;country;latitude;longitude."""
    return util.cwbset(";".join((y[0], y[3], y[1], y[2])) for x, y in location_data)


if __name__ == '__main__':
    util.run.main(build_model)
