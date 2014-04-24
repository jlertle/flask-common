import calendar
import codecs
import csv
import cStringIO
import datetime
import pytz
import re
import signal
import smtplib
import sys
import thread
import threading
import traceback
import unidecode
import StringIO

from blist import sortedset
from email.utils import formatdate
from flask import current_app, request, Response
from flask.ext.mail import Message
from functools import wraps
from itertools import chain
from logging.handlers import SMTPHandler
from mongoengine.context_managers import query_counter
from smtplib import SMTPDataError
from socket import gethostname


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

class isortedset(sortedset):
    def __init__(self, *args, **kwargs):
        if not kwargs.get('key'):
            kwargs['key'] = lambda s: s.lower()
        super(isortedset, self).__init__(*args, **kwargs)

    def __contains__(self, key):
        if not self:
            return False
        try:
            return self[self.bisect_left(key)].lower() == key.lower()
        except IndexError:
            return False

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
        self.queue = cStringIO.StringIO()
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


class Enum(object):
    @classmethod
    def choices(cls):
        return [(getattr(cls,v), v) for v in dir(cls) if not callable(getattr(cls,v)) and not (v.startswith('__') and v.endswith('__'))]


def grouper(n, iterable):
    # e.g. 2, [1, 2, 3, 4, 5] -> [[1, 2], [3, 4], [5]]
    return [iterable[i:i+n] for i in range(0, len(iterable), n)]


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

    # "A datetime object d is aware if d.tzinfo is not None and
    # d.tzinfo.utcoffset(d) does not return None."
    # - http://docs.python.org/2/library/datetime.html
    if d.tzinfo is not None and d.tzinfo.utcoffset(d) is not None:
        return d.astimezone(pytz.utc).replace(tzinfo=None)
    else:
        return d.replace(tzinfo=None)


def mail_admins(subject, body, recipients=None):
    if recipients == None:
        recipients = current_app.config['ADMINS']
    if not current_app.testing:
        if current_app.debug:
            print 'Sending mail_admins:'
            print 'Subject: {0}'.format(subject)
            print
            print body
        else:
            current_app.mail.send(Message(
                subject,
                sender=current_app.config['SERVER_EMAIL'],
                recipients=recipients,
                body=body,
            ))


def mail_exception(extra_subject=None, context=None, vars=True, subject=None, recipients=None):
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
        tb = exc_info[2]
        stack = []

        while tb:
            stack.append(tb.tb_frame)
            tb = tb.tb_next

        message_vars += "Locals by frame, innermost last:\n"

        for frame in stack:
            message_vars += "\nFrame %s in %s at line %s\n" % (frame.f_code.co_name,
                                                 frame.f_code.co_filename,
                                                 frame.f_lineno)
            for key, value in frame.f_locals.items():
                message_vars += "\t%16s = " % key
                # We have to be careful not to cause a new error in our error
                # printer! Calling repr() on an unknown object could cause an
                # error we don't want.
                try:
                    message_vars += '%s\n' % repr(value)
                except:
                    message_vars += "<ERROR WHILE PRINTING VALUE>\n"

        message_vars += '\n\n\n'


    message_tb = '\n'.join(traceback.format_exception(*exc_info))

    message = ''.join([message_context, message_vars, message_tb])

    recipients = recipients if recipients else current_app.config['ADMINS']

    if not current_app.testing:
        if current_app.debug:
            print 'Sending mail_exception:'
            print 'Subject: {0}'.format(subject)
            print
            print message
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


# Semaphore implementation from Python 3 which supports timeouts.
class Semaphore(threading._Verbose):

    # After Tim Peters' semaphore class, but not quite the same (no maximum)

    def __init__(self, value=1, verbose=None):
        if value < 0:
            raise ValueError("semaphore initial value must be >= 0")
        threading._Verbose.__init__(self, verbose)
        self._cond = threading.Condition(threading.Lock())
        self._value = value

    def acquire(self, blocking=True, timeout=None):
        if not blocking and timeout is not None:
            raise ValueError("can't specify timeout for non-blocking acquire")
        rc = False
        endtime = None
        self._cond.acquire()
        while self._value == 0:
            if not blocking:
                break
            if __debug__:
                self._note("%s.acquire(%s): blocked waiting, value=%s",
                           self, blocking, self._value)
            if timeout is not None:
                if endtime is None:
                    endtime = threading._time() + timeout
                else:
                    timeout = endtime - threading._time()
                    if timeout <= 0:
                        break
            self._cond.wait(timeout)
        else:
            self._value = self._value - 1
            if __debug__:
                self._note("%s.acquire: success, value=%s",
                           self, self._value)
            rc = True
        self._cond.release()
        return rc

    __enter__ = acquire

    def release(self):
        self._cond.acquire()
        self._value = self._value + 1
        if __debug__:
            self._note("%s.release: success, value=%s",
                       self, self._value)
        self._cond.notify()
        self._cond.release()

    def __exit__(self, t, v, tb):
        self.release()


class ThreadedTimer(object):
    """
    Timer class with an optional threaded timer. By default, interrupts the
    main thread with a KeyboardInterrupt.
    """

    def __init__(self, timeout=None, on_timeout=None):
        self.timeout = timeout
        self.on_timeout = on_timeout or self._timeout_handler

    def _timeout_handler(self):
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


def uniqify(seq):
    # preserves order
    seen = set()
    return [ x for x in seq if x not in seen and not seen.add(x)]


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

        s = StringIO.StringIO(line)
        seq = [x.strip() for x in unicode_csv_reader(s).next()]
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
    return dict(list(chain.from_iterable([[(token if case_sensitive else token.lower(), normalization.normalized_form) for token in normalization.tokens] for normalization in normalizations])))


class custom_query_counter(query_counter):
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
        ignore_query = {"ns": {"$nin": self.get_ignored_collections()}}
        return self.db.system.profile.find(ignore_query)

    def _get_count(self):
        """ Get the number of queries. """
        queries = self._get_queries()
        if self.verbose:
            print '-'*80
            for query in queries:
                print query['ns'], '[{}]'.format(query['op']), query.get('query')
                print
            print '-'*80
        count = queries.count()
        return count

