# -*- coding: utf-8 -*-

"""
Tools for exporting, encoding and aligning corpora for Corpus Workbench.
"""

import os
#from tempfile import TemporaryFile
from glob import glob
from collections import defaultdict
import xml.etree.cElementTree as etree

import util

ALIGNDIR = "annotations/align"
UNDEF = "__UNDEF__"

CWB_ENCODING = os.environ.get("CWB_ENCODING", "utf8")
CWB_DATADIR = os.environ.get("CWB_DATADIR")
CORPUS_REGISTRY = os.environ.get("CORPUS_REGISTRY")


######################################################################
# Saving as Corpus Workbench data file

def chain(annotations, default=None):
    """Create a functional composition of a list of annotations.
    E.g., token.sentence + sentence.id -> token.sentence-id
    """
    def follow(key):
        for annot in annotations:
            try:
                key = annot[key]
            except KeyError:
                return default
        return key
    return dict((key, follow(key)) for key in annotations[0])


def export(format, out, order, annotations_columns, annotations_structs, fileid=None, fileids=None, valid_xml=True, columns=(), structs=(), encoding=CWB_ENCODING):
    """
    Export 'annotations' to the VRT or XML file 'out'.
    The order of the annotation keys is decided by the annotation 'order'.
    The columns to be exported are taken from 'columns', default all 'annotations'.
    The structural attributes are specified by 'structs', default no structs.
    If an attribute in 'columns' or 'structs' is "-", that annotation is skipped.
    The structs are specified by "elem:attr", giving <elem attr=N> xml tags.
    """
    assert format in ("vrt", "xml"), "Wrong format specified"
    if isinstance(annotations_columns, basestring):
        annotations_columns = annotations_columns.split()
    if isinstance(annotations_structs, basestring):
        annotations_structs = [x.split(":") for x in annotations_structs.split()]
    if isinstance(columns, basestring):
        columns = columns.split()
    structs_count = len(structs.split())
    structs = parse_structural_attributes(structs)
    
    assert len(annotations_columns) == len(columns), "columns and annotations_columns must contain same number of values"
    assert len(annotations_structs) == structs_count, "structs and annotations_structs must contain same number of values"
    
    if isinstance(valid_xml, basestring):
        valid_xml = (valid_xml.lower() == "true")

    # Create parent dictionaries
    parents = {}
    for annot, parent_annotation in annotations_structs:
        if not parent_annotation in parents:
            parents[parent_annotation] = util.read_annotation(parent_annotation)
    
    vrt = defaultdict(dict)
    
    for n, annot in enumerate(annotations_structs):
        token_annotations = chain([parents[annot[1]], util.read_annotation(annot[0])])
        for tok, value in token_annotations.iteritems():
            value = "|" if value == "|/|" else value
            vrt[tok][n] = value.replace("\n", " ") if value else ""
    
    for n, annot in enumerate(annotations_columns):
        n += structs_count
        for tok, value in util.read_annotation_iteritems(annot):
            if n > structs_count:  # Any column except the first (the word)
                value = "|" if value == "|/|" else value
            vrt[tok][n] = value.replace("\n", " ")

    sortkey = util.read_annotation(order).get
    tokens = sorted(vrt, key=sortkey)
    column_nrs = [n+structs_count for (n, col) in enumerate(columns) if col and col != "-"]

    if format == "vrt":
        write_vrt(out, structs, structs_count, columns, column_nrs, tokens, vrt, encoding)
    elif format == "xml":
        write_xml(out, structs, structs_count, columns, column_nrs, tokens, vrt, fileid, fileids, valid_xml, encoding)


def write_xml(out, structs, structs_count, columns, column_nrs, tokens, vrt, fileid, fileids, valid_xml, encoding):
    """ The XML part of the 'export' function: write annotations to a valid xml file, unless valid_xml == False."""

    assert fileid, "fileid not specified"
    assert fileids, "fileids not specified"

    fileid = util.read_annotation(fileids)[fileid].encode(encoding)
    overlap = False
    open_tag_stack = []
    pending_tag_stack = []
    str_buffer = ["<corpus>"]
    elemid = 0
    elemids = {}
    invalid_str_buffer = ["<corpus>"]
    old_attr_values = dict((elem, None) for (elem, _attrs) in structs)
    for tok in tokens:
        cols = vrt[tok]
        new_attr_values = {}
    
        # Close tags/fix overlaps
        for elem, attrs in structs:
            new_attr_values[elem] = [(attr, cols[n]) for (attr, n) in attrs if cols.get(n)]
            if old_attr_values[elem] and new_attr_values[elem] != old_attr_values[elem]:
                if not valid_xml:
                    invalid_str_buffer.append("</%s>" % elem.encode(encoding))
                
                # Check for overlap
                while elem != open_tag_stack[-1][0]:
                    overlap = True
                    # Close top stack element, remember to re-open later
                    str_buffer.append("</%s>" % open_tag_stack[-1][0].encode(encoding))
                    pending_tag_stack.append(open_tag_stack.pop())

                # Fix pending tags
                while pending_tag_stack:
                    if elem == open_tag_stack[-1][0]:
                        str_buffer.append("</%s>" % elem.encode(encoding))
                        open_tag_stack.pop()
                    # Re-open pending tag
                    pending_elem, attrstring = pending_tag_stack[-1]
                    if not elemids.get(pending_elem):
                        elemid += 1
                        elemids[pending_elem] = elemid
                    line = '<%s _id="%s-%s"%s>' % (pending_elem.encode(encoding), fileid, elemids[pending_elem], attrstring)
                    str_buffer.append(line)
                    open_tag_stack.append(pending_tag_stack.pop())
                    old_attr_values[elem] = None

                # Close last open tag from overlap
                if elem == open_tag_stack[-1][0] and not pending_tag_stack:
                    str_buffer.append("</%s>" % elem.encode(encoding))
                    open_tag_stack.pop()
                    old_attr_values[elem] = None
                    elemids = {}

        # Open tags
        for elem, _attrs in reversed(structs):
            if new_attr_values[elem] and new_attr_values[elem] != old_attr_values[elem]:
                attrstring = ''.join(' %s="%s"' % (attr, val.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;"))
                                     for (attr, val) in new_attr_values[elem] if not attr == UNDEF).encode(encoding)
                line = "<%s%s>" % (elem.encode(encoding), attrstring)
                str_buffer.append(line)
                old_attr_values[elem] = new_attr_values[elem]
                open_tag_stack.append((elem, attrstring))
                if not valid_xml:
                    invalid_str_buffer.append("<%s%s>" % (elem.encode(encoding), attrstring))

        # Add word annotations
        word = cols.get(structs_count, UNDEF).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        attributes = " ".join('%s="%s"' % (columns[n-structs_count], cols.get(n, UNDEF).replace("&", "&amp;").replace('"', '&quot;').replace("<", "&lt;").replace(">", "&gt;")) for n in column_nrs[1:])
        line = "<w %s>%s</w>" % (attributes, word)
        str_buffer.append(remove_control_characters(line).encode(encoding))
        if not valid_xml:
            invalid_str_buffer.append(remove_control_characters(line).encode(encoding))

    # Close remaining open tags
    if open_tag_stack:
        for elem in reversed(open_tag_stack):
            str_buffer.append("</%s>" % elem[0].encode(encoding))
    if not valid_xml:
        for elem, _attrs in structs:
            if old_attr_values[elem]:
                invalid_str_buffer.append("</%s>" % elem.encode(encoding))

    str_buffer.append("</corpus>")
    invalid_str_buffer.append("</corpus>")

    # Convert str_buffer list to string
    str_buffer = "\n".join(str_buffer)
    invalid_str_buffer = "\n".join(invalid_str_buffer)

    if not valid_xml:
        # Write string buffer to invalid xml file
        with open(out, "w") as OUT:
            print >>OUT, invalid_str_buffer
    elif not overlap:
        # Write string buffer
        with open(out, "w") as OUT:
            print >>OUT, str_buffer
    else:
        # Go through xml structure and add missing _id attributes
        xmltree = etree.ElementTree(etree.fromstring(str_buffer))
        for child in xmltree.getroot().iter():
            # If child has and id, get previous element with same tag
            if child.tag != "w" and child.attrib.get("_id"):
                elemlist = list(xmltree.getroot().iter(child.tag))
                if child != elemlist[0]:
                    prev_elem = elemlist[elemlist.index(child) - 1]
                    # If previous element has no id, add id of child
                    if not prev_elem.attrib.get("_id"): 
                        prev_elem.set('_id', child.attrib.get("_id"))
        xmltree.write(out, encoding=encoding, xml_declaration=False, method="xml")

    util.log.info("Exported %d tokens, %d columns, %d structs: %s", len(tokens), len(column_nrs), len(structs), out)


def write_vrt(out, structs, structs_count, columns, column_nrs, tokens, vrt, encoding):
    """ The VRT part of the 'export' function: write annotations to vrt file 'out'.""" 
    with open(out, "w") as OUT:
        old_attr_values = dict((elem, None) for (elem, _attrs) in structs)
        for tok in tokens:
            cols = vrt[tok]
            new_attr_values = {}
            for elem, attrs in structs:
                new_attr_values[elem] = [(attr, cols[n]) for (attr, n) in attrs if cols.get(n)]
                if old_attr_values[elem] and new_attr_values[elem] != old_attr_values[elem]:
                    print >>OUT, "</%s>" % elem.encode(encoding)
                    old_attr_values[elem] = None

            for elem, _attrs in reversed(structs):
                if new_attr_values[elem] and new_attr_values[elem] != old_attr_values[elem]:
                    attrstring = ''.join(' %s="%s"' % (attr, val.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;"))
                                         for (attr, val) in new_attr_values[elem] if not attr == UNDEF).encode(encoding)
                    print >>OUT, "<%s%s>" % (elem.encode(encoding), attrstring)
                    old_attr_values[elem] = new_attr_values[elem]
            
            # Whitespace and / needs to be replaced for CQP parsing to work. / is only allowed in the word itself.
            line = "\t".join(cols.get(n, UNDEF).replace(" ", "_").replace("/", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if n > structs_count else cols.get(n, UNDEF).replace(" ", "_").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for n in column_nrs)
            print >>OUT, remove_control_characters(line).encode(encoding)

        for elem, _attrs in structs:
            if old_attr_values[elem]:
                print >>OUT, "</%s>" % elem.encode(encoding)

    util.log.info("Exported %d tokens, %d columns, %d structs: %s", len(tokens), len(column_nrs), len(structs), out)


def combine_xml(master, out, xmlfiles="", xmlfiles_list=""):
    assert master != "", "Master not specified"
    assert out != "", "Outfile not specified"
    assert (xmlfiles or xmlfiles_list), "Missing source"

    if xmlfiles:
        if isinstance(xmlfiles, basestring): xmlfiles = xmlfiles.split()
    elif xmlfiles_list:
        with open(xmlfiles_list) as insource:
            xmlfiles = [line.strip() for line in insource]
    
    xmlfiles.sort()
    
    with open(out, "w") as OUT:
        print >>OUT, '<corpus id="%s">' % master.replace("&", "&amp;").replace('"', '&quot;')
        for infile in xmlfiles:
            util.log.info("Read: %s", infile)
            with open(infile, "r") as IN:
                # Append everything but <corpus> and </corpus>
                print >>OUT, IN.read()[9:-10],
        print >>OUT, "</corpus>"
        util.log.info("Exported: %s" % out)


def cwb_encode(master, columns, structs=(), vrtdir=None, vrtfiles=None,
               encoding=CWB_ENCODING, datadir=CWB_DATADIR, registry=CORPUS_REGISTRY, skip_compression=False, skip_validation=False):
    """
    Encode a number of VRT files, by calling cwb-encode.
    params, structs describe the attributes that are exported in the VRT files.
    """
    assert master != "", "Master not specified"
    assert bool(vrtdir) != bool(vrtfiles), "Either VRTDIR or VRTFILES must be specified"
    assert datadir, "CWB_DATADIR not specified"
    assert registry, "CORPUS_REGISTRY not specified"
    if isinstance(skip_validation, basestring):
        skip_validation = (skip_validation.lower() == "true")
    if isinstance(skip_compression, basestring):
        skip_compression = (skip_compression.lower() == "true")
    if isinstance(vrtfiles, basestring):
        vrtfiles = vrtfiles.split()
    if isinstance(columns, basestring):
        columns = columns.split()
    structs = parse_structural_attributes(structs)

    corpus_registry = os.path.join(registry, master)
    corpus_datadir = os.path.join(datadir, master)
    util.system.clear_directory(corpus_datadir)
    
    encode_args = ["-s", "-p", "-",
                   "-d", corpus_datadir,
                   "-R", corpus_registry,
                   "-c", encoding,
                   "-x"
                   ]
    if vrtdir:
        encode_args += ["-F", vrtdir]
    if vrtfiles:
        for vrt in vrtfiles:
            encode_args += ["-f", vrt]
    for col in columns:
        if col != "-":
            encode_args += ["-P", col]
    for struct, attrs in structs:
        attrs2 = "+".join(attr for attr, _n in attrs if not attr == UNDEF)
        if attrs2:
            attrs2 = "+" + attrs2
        encode_args += ["-S", "%s:0%s" % (struct, attrs2)]
    util.system.call_binary("cwb-encode", encode_args, verbose=True)

    index_args = ["-V", "-r", registry, master.upper()]
    util.system.call_binary("cwb-makeall", index_args, verbose=True)
    util.log.info("Encoded and indexed %d columns, %d structs", len(columns), len(structs))
    
    if not skip_compression:
        util.log.info("Compressing corpus files...")
        compress_args = ["-A", master.upper()]
        if skip_validation:
            compress_args.insert(0, "-T")
            util.log.info("Skipping validation")
        # Compress token stream
        util.system.call_binary("cwb-huffcode", compress_args)
        util.log.info("Removing uncompressed token stream...")
        for f in glob(os.path.join(corpus_datadir, "*.corpus")):
            os.remove(f)
        # Compress index files
        util.system.call_binary("cwb-compress-rdx", compress_args)
        util.log.info("Removing uncompressed index files...")
        for f in glob(os.path.join(corpus_datadir, "*.corpus.rev")):
            os.remove(f)
        for f in glob(os.path.join(corpus_datadir, "*.corpus.rdx")):
            os.remove(f)
        util.log.info("Compression done.")


def cwb_align(master, other, link, aligndir=ALIGNDIR):
    """
    Align 'master' corpus with 'other' corpus, using the 'link' annotation for alignment.
    """

    util.system.make_directory(aligndir)
    alignfile = os.path.join(aligndir, master + ".align")
    util.log.info("Aligning %s <-> %s", master, other)

    try:
        [(link_name, [(link_attr, _path)])] = parse_structural_attributes(link)
    except ValueError:
        raise ValueError("You have to specify exactly one alignment link.")
    link_attr = link_name + "_" + link_attr

    # Align linked chunks
    args = ["-v", "-o", alignfile, "-V", link_attr, master, other, link_name]
    result, _ = util.system.call_binary("cwb-align", args, verbose=True)
    with open(alignfile + ".result", "w") as F:
        print >>F, result
    _, lastline = result.rsplit("Alignment complete.", 1)
    util.log.info("%s", lastline.strip())
    if " 0 alignment" in lastline.strip():
        util.log.warning("No alignment regions created")
    util.log.info("Alignment file/result: %s/.result", alignfile)

    # Add alignment parameter to registry
    # cwb-regedit is not installed by default, so we skip it and modify the regfile directly instead:
    regfile = os.path.join(os.environ["CORPUS_REGISTRY"], master)
    with open(regfile, "r") as F:
        skip_align = ("ALIGNED %s" % other) in F.read()

    if not skip_align:
        with open(regfile, "a") as F:
            print >>F
            print >>F, "# Added by cwb.py"
            print >>F, "ALIGNED", other
        util.log.info("Added alignment to registry: %s", regfile)
    # args = [master, ":add", ":a", other]
    # result, _ = util.system.call_binary("cwb-regedit", args, verbose=True)
    # util.log.info("%s", result.strip())

    # Encode the alignments into CWB
    args = ["-v", "-D", alignfile]
    result, _ = util.system.call_binary("cwb-align-encode", args, verbose=True)
    util.log.info("%s", result.strip())


def parse_structural_attributes(structural_atts):
    if isinstance(structural_atts, basestring):
        structural_atts = structural_atts.split()
    structs = {}
    order = []
    for n, struct in enumerate(structural_atts):
        assert not struct or struct == "-" or "." not in struct, "Struct should contain ':' or be equal to '-': %s" % struct
        
        if ":" in struct:
            elem, attr = struct.split(":")
        else:
            elem = struct
            attr = UNDEF
        if struct and not struct == "-":
            if elem not in structs:
                structs[elem] = []
                order.append(elem)
            structs[elem].append((attr, n))
    return [(elem, structs[elem]) for elem in order]


def remove_control_characters(text):
    return text.translate(dict((ord(c), None) for c in [chr(i) for i in range(9) + range(11, 13) + range(14, 32) + [127]]))


if __name__ == '__main__':
    util.run.main(export=export,
                  encode=cwb_encode,
                  align=cwb_align,
                  combine_xml=combine_xml)

