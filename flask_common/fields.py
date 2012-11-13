import pytz
from mongoengine.fields import StringField, BinaryField
from phonenumbers import PhoneNumber
from phonenumbers.phonenumberutil import format_number, parse, PhoneNumberFormat, NumberParseException
from bson import Binary
from Crypto.Cipher import AES
from Crypto import Random
import Padding


class TrimmedStringField(StringField):
    def __init__(self, *args, **kwargs):
        kwargs['required'] = kwargs.get('required', False) or kwargs.get('min_length', 0) > 0
        return super(TrimmedStringField, self).__init__(*args, **kwargs)

    def validate(self, value):
        super(TrimmedStringField, self).validate(value)
        if self.required and not value:
            self.error('Value cannot be blank.')

    def __set__(self, instance, value):
        value = self.to_python(value)
        return super(TrimmedStringField, self).__set__(instance, value)

    def to_python(self, value):
        if value:
            value = value.strip()
        return value

    def to_mongo(self, value):
        return self.to_python(value)


class TimezoneField(StringField):
    def __init__(self, *args, **kwargs):
        defaults = {
            'default': 'UTC',
            'choices': tuple(zip(pytz.all_timezones, pytz.all_timezones)),
        }
        defaults.update(kwargs)
        return super(TimezoneField, self).__init__(*args, **defaults)

    def to_python(self, value):
        return pytz.timezone(value)

    def to_mongo(self, value):
        return unicode(value)


class PhoneField(StringField):
    """
    Field that performs phone number validation.
    Values are stored in the format "+14151231234x123" in MongoDB and displayed
    in the format "+1 415-123-1234 ext. 123" in Python.
    """

    def _parse(self, value):
        return parse(value, 'US')

    def __set__(self, instance, value):
        value = self.to_python(value)
        return super(PhoneField, self).__set__(instance, value)

    def validate(self, value):
        if not self.required and not value:
            return None
        else:
            try:
                self._parse(value)
            except NumberParseException:
                self.error('Phone is not valid')

    def to_python(self, value):
        return self.to_raw_phone(value)

    def to_mongo(self, value):
        return self.to_raw_phone(value)

    def to_formatted_phone(self, value):
        if isinstance(value, basestring) and value != '':
            try:
                phone = self._parse(value)
                value = format_number(phone, PhoneNumberFormat.INTERNATIONAL)
            except NumberParseException:
                pass
        return value

    def to_raw_phone(self, value):
        if isinstance(value, basestring) and value != '':
            try:
                phone = self._parse(value)
                value = format_number(phone, PhoneNumberFormat.E164)
                if phone.extension:
                    value += 'x%s' % phone.extension
            except NumberParseException:
                pass
        return value

    def prepare_query_value(self, op, value):
        return self.to_raw_phone(value)


rng = Random.new().read

class EncryptedStringField(BinaryField):
    """
    Encrypted string field. Uses AES256 bit encryption with a different 128 bit
    IV every time the field is saved. Encryption is completely transparent to
    the user as the field automatically unencrypts when the field is accessed
    and encrypts when the document is saved.
    """

    IV_SIZE = 16

    def __init__(self, key, *args, **kwargs):
        """
        Key: 32 byte binary string containing the 256 bit AES key
        """
        self.key = key
        return super(EncryptedStringField, self).__init__(*args, **kwargs)

    def _encrypt(self, data):
        iv = rng(self.IV_SIZE)
        ret = Binary(iv + AES.new(self.key, AES.MODE_CBC, iv).encrypt(Padding.appendPadding(data)))
        return ret

    def _decrypt(self, data):
        iv, cipher = data[:self.IV_SIZE], data[self.IV_SIZE:]
        return Padding.removePadding(AES.new(self.key, AES.MODE_CBC, iv).decrypt(cipher))

    def to_python(self, value):
        return value and self._decrypt(value) or None

    def to_mongo(self, value):
        return value and self._encrypt(value) or None
