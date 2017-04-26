# -*- coding: utf-8 -*-

import sb.util as util
import sb.cwb as cwb
from subprocess import Popen, PIPE
import itertools as it
from functools import wraps
from collections import Counter, namedtuple, OrderedDict
import sys
import json
import tempfile
from vowpalwabbit import pyvw
import pylibvw


def predict(model, order, struct, parent, word, out, pos):
    """
    Predict a structural attribute.

    Both model and model.json must exist. See --train.
    """

    m_json = json.load(open(model + '.json'))

    data = (
        Example(None, text.words, text.span)
        for text in texts([(order, struct, parent, word, pos)],
                          map_label=lambda _: '?',
                          min_word_length=m_json['min_word_length'],
                          banned_pos=m_json['banned_pos'])
    )

    index_to_label = m_json['index_to_label']

    args = ['--initial_regressor', model]

    predictions = (
        (span, index_to_label[str(s)])
        for s, span in vw_predict(args, data)
    )

    util.write_annotation(out, predictions)


def make_label_map(label_map_json):
    if label_map_json:
        with open(label_map_json, 'r') as fp:
            d = json.load(fp)
        return lambda label: d.get(label, None)
    else:
        return lambda label: label


def take(bound, xs):
    if bound:
        i = 0
        for x in xs:
            i += 1
            if i >= bound:
                break
            yield x
    else:
        for x in xs:
            yield x


def train(file_list, outprefix,
          dry_run_labels=False, label_map_json=None,
          bound=None, min_word_length=0, banned_pos=''):
    """
    Train a model using vowpal wabbit.

    Creates outprefix.model and outprefix.model.json.

    file_list is a file with 5*N lines of annotation filenames:
    first N copies of: order,
     then N copies of: annotation_struct,
     then N copies of: parent,
     then N copies of: word.
     then N copies of: pos.
    """

    modelfile=outprefix + '.model'
    jsonfile=outprefix + '.model.json'

    with open(file_list, 'r') as fp:
        files = fp.read().split()
    order_struct_parent_word_pos = interleave(files, 5)
    map_label = make_label_map(label_map_json)
    min_word_length = int(min_word_length) if min_word_length else 0

    # Look at the structs annotations to get the labels and their distribution:
    _, structs, _, _, _ = zip(*order_struct_parent_word_pos)
    # todo: skip labels with very low occurrences
    labels = Counter(map_label(label)
                     for annotfile in structs
                     for _tok, label in util.read_annotation_iteritems(annotfile)
                     if map_label(label))
    N = sum(labels.values())
    if bound:
        bound = int(bound)
        N = min(bound, N)
    k = len(labels)
    label_to_index = {}
    index_to_label = {}
    answer = {}
    for i, (label, occurences) in enumerate(labels.iteritems(), start=1):
        w = float(N)/occurences
        util.log.info('%s: occurences: %s, weight: %s', label, occurences, w)
        answer[label] = ('%s:%s | ' % (i, w)).encode()
        label_to_index[label] = i
        index_to_label[i] = label

    if dry_run_labels == 'true':
        from pprint import pprint
        pprint(labels.most_common())
        print(json.dumps({l: l for l in labels}, indent=2))
        util.log.info('texts: %s, labels: %s', N, k)
        import sys
        sys.exit()

    def itertexts():
        return take(bound, texts(order_struct_parent_word_pos, map_label, min_word_length, banned_pos))

    # Train model
    args = ['--oaa', str(k),
            '--passes', '10',
            '--cache', '--kill_cache',
            '--bit_precision', '24',
            '--final_regressor', modelfile]
    data = (
        Example(answer[text.label], text.words)
        for text in every(10, itertexts(), invert=True)
    )
    vw_train(args, data)

    # Performance evaluation
    args = ['--initial_regressor', modelfile]
    target = []
    def data():
        for text in every(10, itertexts()):
            target.append(label_to_index[text.label])
            yield Example(None, text.words)
    predicted = [int(s) for s, _tag in vw_predict(args, data())]
    N_eval = len(predicted)

    assert len(predicted) == len(target)

    order = list(range(1, 1+k))
    info = dict(
        min_word_length = min_word_length,
        banned_pos = banned_pos,
        labels = [index_to_label[i] for i in order],
        index_to_label = index_to_label,
        label_to_index = label_to_index,
        N_train = N - N_eval,
        N_eval = N_eval,
        stats = {index_to_label[i]: p.as_dict()
                 for i, p in
                 multiclass_performance(target, predicted).iteritems()},
        confusion_matrix = confusion_matrix(target, predicted, order))
    with open(jsonfile, 'w') as f:
        json.dump(info, f, sort_keys=True, indent=2)
    util.log.info('Wrote ' + jsonfile)


def make_testdata(corpus_desc='abcd abcd dcba cbad', docs=1000):
    """
    Write amazing test data on stdout.
    """
    import random
    n_docs = int(docs)
    make = lambda s: (s, triangulate(s))
    corpuses = [(s, tuple(triangulate(s))) for s in corpus_desc.split()]
    for _ in range(n_docs):
        corpus, freq = random.choice(corpuses)
        print('<text label="' + corpus + '">')
        n_words = random.randint(12, 39)
        print(' '.join(random.choice(freq) for _ in range(n_words)))
        print('</text>')


Text = namedtuple('Text', 'label span words')


def texts(order_struct_parent_word_pos, map_label, min_word_length, banned_pos):
    """
    Get all the texts from an iterator of 5-tuples of annotations that
    contain token order, the structural attribute (like a label),
    its parenting of the words, and the words themselves.
    """
    X = 0
    S = 0
    banned_pos = (banned_pos or '').split()
    for order, struct, parent, word, pos in order_struct_parent_word_pos:
        x = 0
        s = 0
        util.log.info("Processing %s %s %s", struct, parent, word)
        tokens, vrt = cwb.tokens_and_vrt(order, [(struct, parent)], [word, pos])
        for (label, span), cols in cwb.vrt_iterate(tokens, vrt):
            words = b' '.join(vw_normalize(col[0])
                              for col in cols
                              if len(col[0]) >= min_word_length
                              if col[1] not in banned_pos)
            mapped_label = map_label(label)
            if mapped_label:
                x += 1
                yield Text(mapped_label, span, words)
            else:
                s += 1
        util.log.info("Texts from %s: %s (skipped: %s)", struct, x, s)
        X += x
        S += s
    util.log.info("Total texts: %s (skipped: %s)", X, S)


Example = namedtuple('Example', 'label features tag')
Example.__new__.__defaults__ = (None,)


def vw_train(args, data):
    """
    Train using vowpal wabbit on an iterator of Examples.

    >>> with tempfile.NamedTemporaryFile() as tmp:
    ...     _ = vw_train(['--final_regressor', tmp.name, '--oaa', '2', '--quiet'],
    ...                  [Example('1', 'a b c'), Example('2', 'c d e')])
    ...     list(vw_predict(['--initial_regressor', tmp.name, '--quiet'],
    ...                     [Example(None, 'a b c', 'abc_tag'),
    ...                      Example(None, 'c d e', 'cde_tag')]))
    ...     list(vw_predict(['--initial_regressor', tmp.name, '--quiet'],
    ...                     [Example(None, 'a b c', 'abc_tag'),
    ...                      Example(None, 'c d e', 'cde_tag')],
    ...                     raw=True))
    [(1, 'abc_tag'), (2, 'cde_tag')]
    ['1:0.343459 2:-0.343459', '1:-0.334946 2:0.334946']
    """
    tuple(_vw_run(args, data, False)) # force evaluation using tuple


def vw_predict(args, data, raw=False):
    """
    Predict using vowpal wabbit on an iterator of Examples.

    Argument list is adjusted for testing and getting predictions as a stream.
    """
    if raw:
        with tempfile.NamedTemporaryFile() as tmp:
            more_args = ['--testonly', '-r', tmp.name]
            for _ in _vw_run(args + more_args, data, True):
                pass
            return open(tmp.name, 'r').read().rstrip().split('\n')
    else:
        more_args = ['--testonly']
        return _vw_run(args + more_args, data, True)


def _vw_run(args, data, predict_and_yield):
    vw = pyvw.vw(' '.join(args))
    util.log.info('Running: vw ' + ' '.join(args))
    for d in data:
        ex = vw.example((d.label or b'') + b' | ' + d.features + b'\n')
        if predict_and_yield:
            yield vw.predict(ex, pylibvw.vw.lMulticlass), d.tag
        else:
            vw.learn(ex)
    vw.finish()


def vw_normalize(s):
    u"""
    Normalize a string so it can be used as a VW feature.

    >>> print(vw_normalize(u'VW | abcåäö:123').decode('utf-8'))
    vwSISabcåäöCXXX
    """

    return s.lower().translate(_escape_table).encode('utf-8')


_escape_symbols = [
    # Replace digits with X
     (unicode(x), u'X') for x in range(10)
    ] + map(tuple, (
        # Vowpal Wabbit needs these to be escaped:
        u' S', # separates features
        u'|I', # separates namespaces
        u':C'  # separates feature and its value
    ))
_escape_table={ord(k): v for k, v in _escape_symbols}


### Performance


class Performance(object):
    def __init__(self, TP, TN, FP, FN):
        """
        Performance measures from how many of true and false positives and negatives.

        https://en.wikipedia.org/wiki/Precision_and_recall
        """
        div = lambda x, y: 0.0 if y == 0 else float(x)/float(y)
        harmonic_mean = lambda x, y: div(2*x*y, x+y)
        n = TP + TN + FP + FN
        self.ACC = div(TP + TN, n)
        self.PRE = div(TP, TP + FP)
        self.REC = div(TP, TP + FN)
        self.PRF = harmonic_mean(self.PRE, self.REC)

    def as_dict(self):
        """Performance statistics in a dictionary."""
        keys = 'ACC PRE REC PRF'.split()
        return OrderedDict((k, self.__dict__[k]) for k in keys)

    def __repr__(self):
        perf = ', '.join('%s=%.3f' % kv for kv in self.as_dict().iteritems())
        return "Performance(" + perf + ")"


def binary_performance(target, predicted):
    """
    Standard performance measures on the predictions of a binary classifier.

    >>> p = binary_performance([1,1,1,1,0,0,0,0],
    ...                        [1,1,1,0,0,1,1,0])
    >>> p.ACC == 5/8.0
    True
    >>> p.PRE == 3/5.0
    True
    >>> p.REC == 3/4.0
    True
    """
    d = Counter(zip(target, predicted))
    return Performance(TP = d[1, 1],
                       TN = d[0, 0],
                       FP = d[0, 1],
                       FN = d[1, 0])


def multiclass_performance(target, predicted):
    """
    >>> multiclass_performance([1,1,1,1,2,2,3,3],
    ...                        [1,1,1,2,2,1,1,3])
    ...                                         # doctest: +NORMALIZE_WHITESPACE
    {1: Performance(ACC=0.625, PRE=0.600, REC=0.750, PRF=0.667),
     2: Performance(ACC=0.750, PRE=0.500, REC=0.500, PRF=0.500),
     3: Performance(ACC=0.875, PRE=1.000, REC=0.500, PRF=0.667)}
    """
    return {
        i: binary_performance((t==i for t in target), (p==i for p in predicted))
        for i in nub(it.chain(target, predicted))
    }


def confusion_matrix(target, predicted, order):
    """
    Confusion matrix with the target on the y-axis and rows on the x-axis.

    https://en.wikipedia.org/wiki/Confusion_matrix

    >>> confusion_matrix([1,1,1,1,2,2,3,3],
    ...                  [1,1,1,2,2,1,1,3],
    ...                  order=[1,2,3])         # doctest: +NORMALIZE_WHITESPACE
    [[3, 1, 0],
     [1, 1, 0],
     [1, 0, 1]]
    """

    matrix = Counter(zip(target, predicted))
    return [[ matrix.get((t, p), 0) for p in order ] for t in order]


### Utility functions


def triangulate(xs):
    """
    All initial segments of xs, concatenated.

    >>> ''.join(triangulate('abc'))
    'aababc'
    >>> ''.join(triangulate('1234'))
    '1121231234'
    >>> list(triangulate([]))
    []
    """
    for i, _ in enumerate(xs):
        for x in xs[:i+1]:
            yield x


def interleave(xs, k):
    """
    Put the elements of xs in k-tuples, with the one same distance
    between consecutive elements in every tuple.

    Does not support infinite xs.

    >>> interleave('abc123', 2)
    [('a', '1'), ('b', '2'), ('c', '3')]
    >>> interleave('abcdABCD1234', 3)
    [('a', 'A', '1'), ('b', 'B', '2'), ('c', 'C', '3'), ('d', 'D', '4')]
    """
    ts = [[] for _ in range(len(xs)/k)]
    ts_iter = it.cycle(ts)
    for x in xs:
        next(ts_iter).append(x)
    return [tuple(t) for t in ts]


def every(sep, generator, invert=False):
    """
    Iterate over the generator, returning every sep element of it.

    >>> list(every(3, 'Dan Malin Maria Martin Anne Jonatan'.split()))
    ['Maria', 'Jonatan']
    >>> ' '.join(every(3, 'Dan Malin Maria Martin Anne Jonatan'.split(), True))
    'Dan Malin Martin Anne'
    """
    for i, x in enumerate(generator):
        if ((i+1) % sep == 0) != invert:
            yield x


def nub(xs):
    """
    All unique elements from xs in order.

    >>> ''.join(nub('apabepa'))
    'apbe'
    >>> import random
    >>> spec = lambda xs: sorted(nub(xs)) == sorted(Counter(xs).keys())
    >>> all(spec(random.sample(range(200), i)) for i in range(100))
    True
    """
    seen = set()
    for x in xs:
        if x not in seen:
            seen.add(x)
            yield x


if __name__ == '__main__':
    import doctest
    util.run.main(train=train, predict=predict, make_testdata=make_testdata,
                  test=lambda verbose=False: doctest.testmod(verbose=verbose))