# -*- coding: utf-8 -*-

"""
Small annotations that don't fit as standalone python files.
"""

import util

def text_spans(text, chunk, out):
    """Add the text content for each edge as a new annotation."""
    corpus_text, anchor2pos, _pos2anchor = util.corpus.read_corpus_text(text)
    OUT = {}
    for edge in util.read_annotation_iterkeys(chunk):
        start = anchor2pos[util.edgeStart(edge)]
        end = anchor2pos[util.edgeEnd(edge)]
        OUT[edge] = corpus_text[start:end]
    util.write_annotation(out, OUT)


def translate_tag(tag, out, mapping):
    """Convert part-of-speech tags, specified by the mapping.
    Example mappings: parole_to_suc, suc_to_simple, ...
    """
    if isinstance(mapping, basestring):
        mapping = util.tagsets.__dict__[mapping]
    util.write_annotation(out, ((n, mapping.get(t,t))
                                for (n, t) in util.read_annotation_iteritems(tag)))


def chain(out, annotations, default=None):
    """Create a functional composition of a list of annotations.
    E.g., token.sentence + sentence.id -> token.sentence-id
    """
    if isinstance(annotations, basestring):
        annotations = annotations.split()
    annotations = [util.read_annotation(a) for a in annotations]
    def follow(key):
        for annot in annotations:
            try: key = annot[key]
            except KeyError: return default
        return key
    util.write_annotation(out, ((key, follow(key)) for key in annotations[0]))


def select(out, annotation, index, separator=None):
    """Select a specific index from the values of an annotation.
    The given annotation values are separated by 'separator',
    default by whitespace, with at least index - 1 elements.
    """
    if isinstance(index, basestring):
        index = int(index)
    util.write_annotation(out, ((key, items.split(separator)[index])
                                for (key, items) in util.read_annotation_iteritems(annotation)))


def constant(chunk, out, value=None):
    """Create an annotation with a constant value for each key."""
    util.write_annotation(out, ((key, value) for key in util.read_annotation_iterkeys(chunk)))
    

if __name__ == '__main__':
    util.run.main(text_spans=text_spans,
                  translate_tag=translate_tag,
                  chain=chain,
                  select=select,
                  constant=constant,
                  )

