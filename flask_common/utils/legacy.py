from __future__ import print_function

import calendar
import codecs
import csv
import io
import datetime
import itertools
import math
import re
import signal
import smtplib
import sys
import threading
import time
import traceback

from email.utils import formatdate
from flask import current_app, request, Response
from functools import wraps
from logging.handlers import SMTPHandler
try:
    import mongoengine
except ImportError:
    mongoengine = None

from smtplib import SMTPDataError
from socket import gethostname


from ..enum import Enum # deprecated


def returns_xml(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        r = f(*args, **kwargs)
        return Response(r, content_type='text/xml; charset=utf-8')
    return decorated_function

def json_list_generator(results):
    """Given a generator of individual JSON results, generate a JSON array"""
    yield '['
    this_val = results.next()
    while True:
        next_val = next(results, None)
        yield this_val + ',' if next_val else this_val
        this_val = next_val
        if not this_val:
            break
    yield ']'


class DetailedSMTPHandler(SMTPHandler):
    def __init__(self, app_name, *args, **kwargs):
        self.app_name = app_name
        return super(DetailedSMTPHandler, self).__init__(*args, **kwargs)

    def getSubject(self, record):
        error = 'Error'
        ei = record.exc_info
        if ei:
            error = '(%s) %s' % (ei[0].__name__, ei[1])
        return "[%s] %s %s on %s" % (self.app_name, request.path, error, gethostname())

    def emit(self, record):
        """
        Emit a record.

        Format the record and send it to the specified addressees.
        """
        try:
            port = self.mailport
            if not port:
                port = smtplib.SMTP_PORT
            smtp = smtplib.SMTP(self.mailhost, port)
            msg = self.format(record)
            msg = "From: %s\nTo: %s\nSubject: %s\nDate: %s\n\n%s\n\nRequest.url: %s\n\nRequest.headers: %s\n\nRequest.args: %s\n\nRequest.form: %s\n\nRequest.data: %s\n" % (
                            self.fromaddr,
                            ",".join(self.toaddrs),
                            self.getSubject(record),
                            formatdate(), msg, request.url, request.headers, request.args, request.form, request.data)
            if self.username:
                if self.secure is not None:
                    smtp.ehlo()
                    smtp.starttls(*self.secure)
                    smtp.ehlo()
                smtp.login(self.username, self.password)
            smtp.sendmail(self.fromaddr, self.toaddrs, msg)
            smtp.quit()
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            self.handleError(record)

def unicode_csv_reader(unicode_csv_data, dialect=csv.excel, **kwargs):
    # csv.py doesn't do Unicode; encode temporarily as UTF-8:
    csv_reader = csv.reader(utf_8_encoder(unicode_csv_data),
                            dialect=dialect, **kwargs)
    for row in csv_reader:
        # decode UTF-8 back to Unicode, cell by cell:
        yield [unicode(cell, 'utf-8') for cell in row]

def utf_8_encoder(unicode_csv_data):
    for line in unicode_csv_data:
        yield line.encode('utf-8')

class CsvReader(object):
    """ Wrapper around csv reader that ignores non utf-8 chars and strips the
    record. """

    def __init__(self, file_name, delimiter=','):
        self.reader = csv.reader(open(file_name, 'rbU'), delimiter=delimiter)

    def __iter__(self):
        return self

    def next(self):
        row = self.reader.next()
        row = [el.decode('utf8', errors='ignore').replace('\"', '').strip() for el in row]
        return row

class NamedCsvReader(CsvReader):
    def __init__(self, *args, **kwargs):
        super(NamedCsvReader, self).__init__(*args, **kwargs)
        self.headers = super(NamedCsvReader, self).next()

    def next(self):
        row = super(NamedCsvReader, self).next()
        return dict(zip(self.headers, row))

class CsvWriter:
    """
    A CSV writer which will write rows to CSV file "f",
    which is encoded in the given encoding.
    From http://docs.python.org/2/library/csv.html
    """
    def __init__(self, f, dialect=csv.excel, encoding="utf-8", **kwds):
        # Redirect output to a queue
        self.queue = io.StringIO()
        self.writer = csv.writer(self.queue, dialect=dialect, **kwds)
        self.stream = f
        self.encoder = codecs.getincrementalencoder(encoding)()

    def writerow(self, row):
        self.writer.writerow([s.encode("utf-8") if isinstance(s, basestring) else s for s in row])
        # Fetch UTF-8 output from the queue ...
        data = self.queue.getvalue()
        data = data.decode("utf-8")
        # ... and reencode it into the target encoding
        data = self.encoder.encode(data)
        # write to the target stream
        self.stream.write(data)
        # empty queue
        self.queue.truncate(0)

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)

def smart_unicode(s, encoding='utf-8', errors='strict'):
    if isinstance(s, unicode):
        return s
    if not isinstance(s, basestring,):
        if hasattr(s, '__unicode__'):
            s = unicode(s)
        else:
            s = unicode(str(s), encoding, errors)
    elif not isinstance(s, unicode):
        s = s.decode(encoding, errors)
    return s

def finite_float(value):
    """Convert any value to a finite float or throw a ValueError if it can't be done."""
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise ValueError("Can't convert %s to a finite float" % value)
    return value


def utctoday():
    now = datetime.datetime.utcnow()
    today = datetime.date(*now.timetuple()[:3])
    return today


def utctime():
    """ Return seconds since epoch like time.time(), but in UTC. """
    return calendar.timegm(datetime.datetime.utcnow().utctimetuple())


def localtoday(tz_or_offset):
    """
    Returns the local today date based on either a timezone object or on a UTC
    offset in hours.
    """
    import pytz
    utc_now = datetime.datetime.utcnow()
    try:
        local_now = tz_or_offset.normalize(pytz.utc.localize(utc_now).astimezone(tz_or_offset))
    except AttributeError: # tz has no attribute normalize, assume numeric offset
        local_now = utc_now + datetime.timedelta(hours=tz_or_offset)
    local_today = datetime.date(*local_now.timetuple()[:3])
    return local_today


def make_unaware(d):
    """ Converts an unaware datetime in UTC or an aware datetime to an unaware
    datetime in UTC. """
    import pytz

    # "A datetime object d is aware if d.tzinfo is not None and
    # d.tzinfo.utcoffset(d) does not return None."
    # - http://docs.python.org/2/library/datetime.html
    if d.tzinfo is not None and d.tzinfo.utcoffset(d) is not None:
        return d.astimezone(pytz.utc).replace(tzinfo=None)
    else:
        return d.replace(tzinfo=None)


def _gen_tz_info_dict():
    """
    Generates the timezone info dict to be passed to dateutil's parse method.
    Since TZ names are ambiguous we prefer the common ones.
    """

    # Adapted from http://stackoverflow.com/questions/1703546/parsing-date-time-string-with-timezone-abbreviated-name-in-python

    tz_str = '''-12 Y
-11 X NUT SST
-10 W CKT HAST HST TAHT TKT
-9.5 MART MIT
-9 V AKST GAMT GIT HADT HNY
-8 U AKDT CIST HAY HNP PST PT
-7 T HAP HNR MST PDT
-6 S CST EAST GALT HAR HNC MDT
-5 R CDT COT EASST ECT EST ET HAC HNE PET
-4.5 HLV VET
-4 Q AST BOT CLT COST EDT FKT GYT HAE HNA PYT
-3.5 HNT NST NT
-3 P ADT ART BRT CLST FKST GFT HAA PMST PYST SRT UYT WGT
-2.5 HAT NDT
-2 O BRST FNT PMDT UYST WGST
-1 N AZOT CVT EGT
0 Z EGST GMT UTC WET WT
1 A CET DFT WAT WEDT WEST IST MEZ
2 B CAT CEDT CEST EET SAST WAST MESZ
3 C EAT EEDT EEST IDT MSK
3.5 IRST
4 D AMT AZT GET GST KUYT MSD MUT RET SAMT SCT
4.5 AFT IRDT
5 E AMST AQTT AZST HMT MAWT MVT PKT TFT TJT TMT UZT YEKT
5.5 SLT
5.75 NPT
6 F ALMT BIOT BTT IOT KGT NOVT OMST YEKST
6.5 CCT MMT
7 G CXT DAVT HOVT ICT KRAT NOVST OMSST THA WIB
8 H ACT AWST BDT BNT CAST HKT IRKT KRAST MYT PHT SGT ULAT WITA WST
9 I AWDT IRKST JST KST PWT TLT WDT WIT YAKT
9.5 ACST
10 K AEST ChST PGT VLAT YAKST YAPT
10.5 ACDT LHST
11 L AEDT LHDT MAGT NCT PONT SBT VLAST VUT
11.5 NFT
12 M ANAST ANAT FJT GILT MAGST MHT NZST PETST PETT TVT WFT
12.75 CHAST
13 FJST NZDT PHOT TOT
13.75 CHADT
14 LINT'''

    tzd = {}
    for tz_descr in map(str.split, tz_str.split('\n')):
        tz_offset = int(float(tz_descr[0]) * 3600)
        for tz_code in tz_descr[1:]:
            assert tz_code not in tzd, "duplicate TZ alias detected"
            tzd[tz_code] = tz_offset
    return tzd

_tz_info_dict = _gen_tz_info_dict()

def parse_date_tz(date):
    """
    Attempts to parse the date, taking common timezone offsets into account. An
    aware or unaware datetime is returned on success, otherwise None.
    """
    import dateutil.parser
    try:
        return dateutil.parser.parse(date, tzinfos=_tz_info_dict)
    except (AttributeError, ValueError):
        return


def mail_admins(subject, body, recipients=None):
    from flask_mail import Message
    if recipients == None:
        recipients = current_app.config['ADMINS']
    if not current_app.testing:
        if current_app.debug:
            print('Sending mail_admins:')
            print('Subject: {0}'.format(subject))
            print()
            print(body)
        else:
            current_app.mail.send(Message(
                subject,
                sender=current_app.config['SERVER_EMAIL'],
                recipients=recipients,
                body=body,
            ))

def format_locals(exc_info):
    tb = exc_info[2]
    stack = []

    message = ''

    while tb:
        stack.append(tb.tb_frame)
        tb = tb.tb_next

    message += 'Locals by frame, innermost last:\n'

    for frame in stack:
        message += '\nFrame %s in %s at line %s\n' % (frame.f_code.co_name,
                                                      frame.f_code.co_filename,
                                                      frame.f_lineno)
        for key, value in frame.f_locals.items():
            message += "\t%16s = " % key
            # We have to be careful not to cause a new error in our error
            # printer! Calling repr() on an unknown object could cause an error
            # we don't want.
            try:
                message += '%s\n' % repr(value)
            except:
                message += "<ERROR WHILE PRINTING VALUE>\n"

    return force_unicode(message)

def mail_exception(extra_subject=None, context=None, vars=True, subject=None, recipients=None):
    from flask_mail import Message

    exc_info = sys.exc_info()

    if not subject:
        subject = "[%s] %s%s %s on %s" % (request.host, extra_subject and '%s: ' % extra_subject or '', request.path, exc_info[1].__class__.__name__, gethostname())

    message_context = ''
    message_vars = ''

    if context:
        message_context += 'Context:\n\n'
        try:
            message_context += '\n'.join(['%s: %s' % (k, context[k]) for k in sorted(context.keys())])
        except:
            message_context += 'Error reporting context.'
        message_context += '\n\n\n\n'

    if vars:
        message_vars += format_locals(exc_info)
        message_vars += '\n\n\n'

    message_tb = '\n'.join(traceback.format_exception(*exc_info))

    message = ''.join([message_context, message_vars, message_tb])

    recipients = recipients if recipients else current_app.config['ADMINS']

    if not current_app.testing:
        if current_app.debug:
            print('Sending mail_exception:')
            print('Subject: {0}'.format(subject))
            print()
            print(message)
        else:
            msg = Message(subject, sender=current_app.config['SERVER_EMAIL'], recipients=recipients)
            msg.body = message
            try:
                current_app.mail.send(msg)
            except SMTPDataError as e:
                # Message too large? Exclude variable info.
                message = ''.join([message_context, 'Not including variable info because we received an SMTP error:\n', repr(e), '\n\n\n\n', message_tb])
                msg.body = message
                current_app.mail.send(msg)


def force_unicode(s):
    """ Return a unicode object, no matter what the string is. """

    if isinstance(s, unicode):
        return s
    try:
        return s.decode('utf8')
    except UnicodeDecodeError:
        # most common encoding, conersion shouldn't fail
        return s.decode('latin1')

def slugify(text, separator='_'):
    import unidecode
    if isinstance(text, unicode):
        text = unidecode.unidecode(text)
    text = text.lower().strip()
    return re.sub(r'\W+', separator, text).strip(separator)


def apply_recursively(obj, f):
    """
    Applies a function to objects by traversing lists/tuples/dicts recursively.
    """
    if isinstance(obj, (list, tuple)):
        return [apply_recursively(item, f) for item in obj]
    elif isinstance(obj, dict):
        return {k: apply_recursively(v, f) for k, v in obj.iteritems()}
    elif obj == None:
        return None
    else:
        return f(obj)

class Timeout(Exception):
    pass

class Timer(object):
    """
    Timer class with an optional signal timer.
    Raises a Timeout exception when the timeout occurs.
    When using timeouts, you must not nest this function nor call it in
    any thread other than the main thread.
    """

    def __init__(self, timeout=None, timeout_message=''):
        self.timeout = timeout
        self.timeout_message = timeout_message

        if timeout:
            signal.signal(signal.SIGALRM, self._alarm_handler)

    def _alarm_handler(self, signum, frame):
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        raise Timeout(self.timeout_message)

    def __enter__(self):
        if self.timeout:
            signal.alarm(self.timeout)
        self.start = datetime.datetime.utcnow()
        return self

    def __exit__(self, *args):
        self.end = datetime.datetime.utcnow()
        delta = (self.end - self.start)
        self.interval = delta.days * 86400 + delta.seconds + delta.microseconds / 1000000.
        if self.timeout:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, signal.SIG_IGN)


class ThreadedTimer(object):
    """
    Timer class with an optional threaded timer. By default, interrupts the
    main thread with a KeyboardInterrupt.
    """

    def __init__(self, timeout=None, on_timeout=None):
        self.timeout = timeout
        self.on_timeout = on_timeout or self._timeout_handler

    def _timeout_handler(self):
        import thread
        thread.interrupt_main()

    def __enter__(self):
        if self.timeout:
            self._timer = threading.Timer(self.timeout, self.on_timeout)
            self._timer.start()
        self.start = datetime.datetime.utcnow()
        return self

    def __exit__(self, *args):
        if self.timeout:
            self._timer.cancel()
        self.end = datetime.datetime.utcnow()
        delta = (self.end - self.start)
        self.interval = delta.days * 86400 + delta.seconds + delta.microseconds / 1000000.


def uniqify(seq, key=lambda i: i):
    """
    Given an iterable, return a list of its unique elements, preserving the
    original order. For example:

    >>> uniqify([1, 2, 3, 1, 'a', None, 'a', 'b'])
    [1, 2, 3, 'a', None, 'b']

    >>> uniqify([ { 'a': 1 }, { 'a': 2 }, { 'a': 1 } ])
    [ { 'a': 1 }, { 'a': 2 } ]

    You can optionally specify a callable as the 'key' parameter which
    can extract or otherwise obtain a key from the items to use as the test for uniqueness.

    For example:
    >>> uniqify([dict(foo='bar', baz='qux'), dict(foo='grill', baz='qux')], key=lambda item: item['baz'])
    [ { 'foo':  'bar', 'baz': 'qux' } ]

    Note: This function doesn't work with nested dicts.
    """
    seen = set()
    result = []
    for x in seq:
        unique_key = key(x)
        if mongoengine and isinstance(unique_key, mongoengine.EmbeddedDocument):
            unique_key = unique_key.to_dict()
        if isinstance(unique_key, dict):
            unique_key = hash(frozenset(unique_key.items()))

        if unique_key not in seen:
            seen.add(unique_key)
            result.append(x)
    return result


### NORMALIZATION UTILS ###

class FileFormatException(Exception):
    pass

class Reader(object):
    """
    Able to interpret files of the form:

        key => value1, value2          [this is the default case where one_to_many=True]
        OR
        value1, value2 => key          [one_to_many=False]


    This is useful for cases where we want to normalize values such as:

        United States, United States of America, 'Merica, USA, U.S. => US

        Minnesota => MN

        Minnesota => MN, Minne

    This reader also can handle quoted values such as:

        "this => that" => "this", that

    """

    def __init__(self, filename):
        self.reader = codecs.open(filename, 'r', 'utf-8')

    def __exit__(self):
        self.reader.close()

    def __iter__(self):
        return self

    @classmethod
    def split(cls, line, one_to_many=True):
        """ return key, values if one_to_many else return values, key """

        def _get(value):
            one, two = value.split('=>', 1)
            return one.strip(), two.strip()

        s = io.StringIO(line)
        # http://stackoverflow.com/questions/6879596/why-is-the-python-csv-reader-ignoring-double-quoted-fields
        seq = [x.strip() for x in unicode_csv_reader(s, skipinitialspace=True).next()]
        if not seq:
            raise FileFormatException("Line does not contain any valid data.")
        if one_to_many:
            key, value = _get(seq.pop(0))
            seq.insert(0, value)
            return key, seq
        else:
            value, key = _get(seq.pop())
            seq.append(value)
            return seq, key

    def next(self, one_to_many=True):
        return Reader.split(self.reader.next(), one_to_many=one_to_many)

class Normalization(object):
    """ list of strings => normalized form """

    def __init__(self, keys, value):
        self.tokens = keys
        self.normalized_form = value

    def merge(self, normalization):
        self.tokens = list(set(self.tokens) | set(normalization.tokens))

class NormalizationReader(Reader):
    """ keys => value """

    def next(self):
        return Normalization(*super(NormalizationReader, self).next(one_to_many=False))

def build_normalization_map(filename, case_sensitive=False):
    normalizations = NormalizationReader(filename)
    return dict(list(itertools.chain.from_iterable([[(token if case_sensitive else token.lower(), normalization.normalized_form) for token in normalization.tokens] for normalization in normalizations])))


if mongoengine:
    class custom_query_counter(mongoengine.context_managers.query_counter):
        """
        Subclass of MongoEngine's query_counter context manager that also lets
        you ignore some of the collections (just extend get_ignored_collections).

        Initialize with custom_query_counter(verbose=True) for debugging.
        """

        def __init__(self, verbose=False):
            super(custom_query_counter, self).__init__()
            self.verbose = verbose

        def get_ignored_collections(self):
            return [
                "{0}.system.indexes".format(self.db.name),
                "{0}.system.namespaces".format(self.db.name),
                "{0}.system.profile".format(self.db.name),
                "{0}.$cmd".format(self.db.name),
            ]


        def _get_queries(self):
            filter_query = { "$or": [
                { "ns": {"$nin": self.get_ignored_collections()}, "op": { "$ne": "killcursors" } },
                { "ns": "{0}.$cmd".format(self.db.name), "command.findAndModify": { "$exists": True } },
            ]}
            return self.db.system.profile.find(filter_query)

        def _get_count(self):
            """ Get the number of queries. """
            queries = self._get_queries()
            if self.verbose:
                print('-'*80)
                for query in queries:
                    # findAndModify appear in $cmd -- we'll make them more readable
                    if query['ns'].endswith('.$cmd'):
                        if 'findAndModify' in query['command']:
                            ns = '.'.join([query['ns'].split('.')[0], query['command']['findAndModify']])
                            op = 'findAndModify'
                            query = query['command'].get('query')
                        else:
                            ns = query['ns']
                            op = query['op']
                            query = query['command']
                    else:
                        ns = query['ns']
                        op = query['op']
                        query = query.get('query')
                    print('{} [{}] {}'.format(ns, op, query))
                    print()
                print('-'*80)
            count = queries.count()
            return count

def truncate(text, size):
    """
    Truncates the given text to the given size. If we are in the middle of
    a word, we will extend until the end of the word, e.g.

    >>> truncate('I can haz cheeseburgers', 9)
     'I can haz'
    >>> truncate('I can haz cheeseburgers', 10)
     'I can haz cheeseburgers'
    """
    if text and text[size:].find(' ') != -1:
        return text[:size+text[size:].find(' ')]
    else:
        return text

def combine(*lists):
    """
    Generate all the combinations for multiple sets of words, e.g.

    >>> combine(['first'], ['communication', 'communicated'], ['', 'date'])
     ['first_communication',
      'first_communication_date',
      'first_communicated',
      'first_communicated_date']
    """
    if len(lists) == 1:
        return lists[0]
    else:
        return ['_'.join([s for s in p if s]) for p in itertools.product(lists[0], combine(*lists[1:]))]

def retry(func=None, exc=Exception, tries=1, wait=0):
    """
    A way to retry a function call up to [tries] times if it throws
    a [exc] exception, with [wait] seconds in between.

    Can be used directly, or as a decorator factory.

    Example Usage 1:
        retry(unreliable_function, exc=ValueError, tries=5, wait=1)

    Example Usage 2 (passing args):
        retry(lambda x, y: unreliable_function(x, y), exc=ValueError, tries=5, wait=1)

    Example Usage 3 (as a decorator generator)
        @retry(exc=ValueError, tries=10, wait=0.3)
        def unreliable_function(foo):
            # ...
        unreliable_function('boy')
    """
    def _retry(func):
        tries_left = tries
        while True:
            try:
                return func()
            except exc:
                tries_left -= 1
                if tries_left <= 0:
                    raise
                time.sleep(wait)

    if func is None:
        # Being used as a decorator generator
        def retry_decorator(func):
            return lambda *args, **kwargs: _retry(lambda: func(*args, **kwargs))
        return retry_decorator
    else:
        # Being used directly
        return _retry(func)

class lazylist(object):
    """
    An object that can be iterated like a list, where the data is only loaded
    from the given function at the first iteration.
    """
    def __init__(self, f):
        self.f = f
        self.data = None

    def __getitem__(self, key):
        if self.data is None:
            self.data = list(self.f())
        return self.data[key]
