"""
Binary protocol specific functions and constants
"""

import sys
import struct
import logging
from datetime import datetime, timedelta, tzinfo, MAXYEAR
from calendar import timegm
import uuid
from enum import IntEnum, Enum

from opcua.ua.uaerrors import UaError
from opcua.common.utils import Buffer
from opcua import ua


if sys.version_info.major > 2:
    unicode = str

logger = logging.getLogger('__name__')

EPOCH_AS_FILETIME = 116444736000000000  # January 1, 1970 as MS file time
HUNDREDS_OF_NANOSECONDS = 10000000
FILETIME_EPOCH_AS_DATETIME = datetime(1601, 1, 1)


def test_bit(data, offset):
    mask = 1 << offset
    return data & mask


def set_bit(data, offset):
    mask = 1 << offset
    return data | mask


def unset_bit(data, offset):
    mask = 1 << offset
    return data & ~mask


class UTC(tzinfo):
    """
    UTC
    """

    def utcoffset(self, dt):
        return timedelta(0)

    def tzname(self, dt):
        return "UTC"

    def dst(self, dt):
        return timedelta(0)


# method copied from David Buxton <david@gasmark6.com> sample code
def datetime_to_win_epoch(dt):
    if (dt.tzinfo is None) or (dt.tzinfo.utcoffset(dt) is None):
        dt = dt.replace(tzinfo=UTC())
    ft = EPOCH_AS_FILETIME + (timegm(dt.timetuple()) * HUNDREDS_OF_NANOSECONDS)
    return ft + (dt.microsecond * 10)


def win_epoch_to_datetime(epch):
    try:
        return FILETIME_EPOCH_AS_DATETIME + timedelta(microseconds=epch // 10)
    except OverflowError:
        # FILETIMEs after 31 Dec 9999 can't be converted to datetime
        logger.warning("datetime overflow: %s", epch)
        return datetime(MAXYEAR, 12, 31, 23, 59, 59, 999999)


def build_array_format_py2(prefix, length, fmtchar):
    return prefix + str(length) + fmtchar


def build_array_format_py3(prefix, length, fmtchar):
    return prefix + str(length) + chr(fmtchar)


if sys.version_info.major < 3:
    build_array_format = build_array_format_py2
else:
    build_array_format = build_array_format_py3


class _Primitive(object):

    def pack_array(self, array):
        if array is None:
            return b'\xff\xff\xff\xff'
        length = len(array)
        b = [self.pack(val) for val in array]
        b.insert(0, Primitives.Int32.pack(length))
        return b"".join(b)

    def unpack_array(self, data):
        length = Primitives.Int32.unpack(data)
        if length == -1:
            return None
        elif length == 0:
            return []
        else:
            return [self.unpack(data) for _ in range(length)]


class _DateTime(_Primitive):

    @staticmethod
    def pack(dt):
        epch = datetime_to_win_epoch(dt)
        return Primitives.Int64.pack(epch)

    @staticmethod
    def unpack(data):
        epch = Primitives.Int64.unpack(data)
        return win_epoch_to_datetime(epch)


class _String(_Primitive):

    @staticmethod
    def pack(string):
        if string is None:
            return Primitives.Int32.pack(-1)
        if isinstance(string, unicode):
            string = string.encode('utf-8')
        length = len(string)
        return Primitives.Int32.pack(length) + string

    @staticmethod
    def unpack(data):
        b = _Bytes.unpack(data)
        if sys.version_info.major < 3:
            return b
        else:
            if b is None:
                return b
            return b.decode("utf-8")


class _Bytes(_Primitive):

    @staticmethod
    def pack(data):
        return _String.pack(data)

    @staticmethod
    def unpack(data):
        length = Primitives.Int32.unpack(data)
        if length == -1:
            return None
        return data.read(length)


class _Null(_Primitive):

    @staticmethod
    def pack(data):
        return b""

    @staticmethod
    def unpack(data):
        return None


class _Guid(_Primitive):

    @staticmethod
    def pack(guid):
        # convert python UUID 6 field format to OPC UA 4 field format
        f1 = Primitives.UInt32.pack(guid.time_low)
        f2 = Primitives.UInt16.pack(guid.time_mid)
        f3 = Primitives.UInt16.pack(guid.time_hi_version)
        f4a = Primitives.Byte.pack(guid.clock_seq_hi_variant)
        f4b = Primitives.Byte.pack(guid.clock_seq_low)
        f4c = struct.pack('>Q', guid.node)[2:8]  # no primitive .pack available for 6 byte int
        f4 = f4a+f4b+f4c
        # concat byte fields
        b = f1+f2+f3+f4

        return b

    @staticmethod
    def unpack(data):
        # convert OPC UA 4 field format to python UUID bytes
        f1 = struct.pack('>I', Primitives.UInt32.unpack(data))
        f2 = struct.pack('>H', Primitives.UInt16.unpack(data))
        f3 = struct.pack('>H', Primitives.UInt16.unpack(data))
        f4 = data.read(8)
        # concat byte fields
        b = f1 + f2 + f3 + f4

        return uuid.UUID(bytes=b)


class _Primitive1(_Primitive):
    def __init__(self, fmt):
        self.struct = struct.Struct(fmt)
        self.size = self.struct.size
        self.format = self.struct.format

    def pack(self, data):
        return struct.pack(self.format, data)

    def unpack(self, data):
        return struct.unpack(self.format, data.read(self.size))[0]
    
    #def pack_array(self, array):
        #"""
        #Basically the same as the method in _Primitive but MAYBE a bit more efficient....
        #"""
        #if array is None:
            #return b'\xff\xff\xff\xff'
        #length = len(array)
        #if length == 0:
            #return b'\x00\x00\x00\x00'
        #if length == 1:
            #return b'\x01\x00\x00\x00' + self.pack(array[0])
        #return struct.pack(build_array_format("<i", length, self.format[1]), length, *array)


class Primitives1(object):
    Int8 = _Primitive1("<b")
    SByte = Int8
    Int16 = _Primitive1("<h")
    Int32 = _Primitive1("<i")
    Int64 = _Primitive1("<q")
    UInt8 = _Primitive1("<B")
    Char = UInt8
    Byte = UInt8
    UInt16 = _Primitive1("<H")
    UInt32 = _Primitive1("<I")
    UInt64 = _Primitive1("<Q")
    Boolean = _Primitive1("<?")
    Double = _Primitive1("<d")
    Float = _Primitive1("<f")


class Primitives(Primitives1):
    Null = _Null()
    String = _String()
    Bytes = _Bytes()
    ByteString = _Bytes()
    CharArray = _String()
    DateTime = _DateTime()
    Guid = _Guid()


def pack_uatype_array(vtype, array):
    if array is None:
        return b'\xff\xff\xff\xff'
    length = len(array)
    b = [pack_uatype(vtype, val) for val in array]
    b.insert(0, Primitives.Int32.pack(length))
    return b"".join(b)


def pack_uatype(vtype, value):
    if hasattr(Primitives, vtype.name):
        return getattr(Primitives, vtype.name).pack(value)
    elif vtype.value > 25:
        return Primitives.Bytes.pack(value)
    elif vtype.name == "ExtensionObject":
        return extensionobject_to_binary(value)
    else:
        try:
            return value.to_binary()
        except AttributeError:
            raise UaError("{0} could not be packed with value {1}".format(vtype, value))


def unpack_uatype(vtype, data):
    if hasattr(Primitives, vtype.name):
        st = getattr(Primitives, vtype.name)
        return st.unpack(data)
    elif vtype.value > 25:
        return Primitives.Bytes.unpack(data)
    elif vtype.name == "ExtensionObject":
        return extensionobject_from_binary(data)
    else:
        from opcua.ua import uatypes
        if hasattr(uatypes, vtype.name):
            klass = getattr(uatypes, vtype.name)
            return klass.from_binary(data)
        else:
            raise UaError("can not unpack unknown vtype {0!s}".format(vtype))


def unpack_uatype_array(vtype, data):
    if hasattr(Primitives, vtype.name):
        st = getattr(Primitives, vtype.name)
        return st.unpack_array(data)
    else:
        length = Primitives.Int32.unpack(data)
        if length == -1:
            return None
        else:
            return [unpack_uatype(vtype, data) for _ in range(length)]


"""
def to_binary(obj):
    if isintance(obj, NodeId):
        return nodeid_to_binary(obj)
    elif hasattr(val, "ua_types"):
        return to_binary(val)
    else:
        raise ValueError("Do not know how to convert {} to binary: {}".format(type(obj), obj))
"""


def struct_to_binary(obj):
    packet = []
    has_switch = hasattr(obj, "ua_switches")
    if has_switch:
        for name, switch in obj.ua_switches.items():
            member = getattr(obj, name)
            container_name, idx = switch
            if member is not None:
                container_val = getattr(obj, container_name)
                container_val = container_val | 1 << idx
                setattr(obj, container_name, container_val)
    for name, uatype in obj.ua_types:
        val = getattr(obj, name)
        if uatype.startswith("ListOf"):
            packet.append(list_to_binary(val, uatype[6:]))
        else:
            if has_switch and val is None and name in obj.ua_switches:
                pass
            else:
                packet.append(to_binary(val, uatype))
    return b''.join(packet)


def to_binary(val, uatype=None):
    print("TB", val, uatype)
    if isinstance(uatype, (str, unicode)) and hasattr(Primitives, uatype):
        st = getattr(Primitives, uatype)
        return st.pack(val)
    elif isinstance(val, (IntEnum, Enum)):
        return Primitives.UInt32.pack(val.value)
    elif isinstance(val, ua.NodeId):
        return nodeid_to_binary(val)
    elif isinstance(val, ua.Header):
        return header_to_binary(val)
    elif isinstance(val, ua.Variant):
        return variant_to_binary(val)
    elif hasattr(val, "ua_types"):
        return struct_to_binary(val)
    elif uatype == "ExtensionObject":
        return extensionobject_to_binary(val)
    else:
        raise ValueError("Cannot pack {} of type {} to ua binary".format(val, uatype))


def list_to_binary(val, uatype):
    if val is None:
        return Primitives.Int32.pack(-1)
    else:
        pack = []
        pack.append(Primitives.Int32.pack(len(val)))
        for el in val:
            pack.append(to_binary(el, uatype))
        return b''.join(pack)


def nodeid_to_binary(nodeid):
    if nodeid.NodeIdType == ua.NodeIdType.TwoByte:
        return struct.pack("<BB", nodeid.NodeIdType.value, nodeid.Identifier)
    elif nodeid.NodeIdType == ua.NodeIdType.FourByte:
        return struct.pack("<BBH", nodeid.NodeIdType.value, nodeid.NamespaceIndex, nodeid.Identifier)
    elif nodeid.NodeIdType == ua.NodeIdType.Numeric:
        return struct.pack("<BHI", nodeid.NodeIdType.value, nodeid.NamespaceIndex, nodeid.Identifier)
    elif nodeid.NodeIdType == ua.NodeIdType.String:
        return struct.pack("<BH", nodeid.NodeIdType.value, nodeid.NamespaceIndex) + \
            Primitives.String.pack(nodeid.Identifier)
    elif nodeid.NodeIdType == ua.NodeIdType.ByteString:
        return struct.pack("<BH", nodeid.NodeIdType.value, nodeid.NamespaceIndex) + \
            Primitives.Bytes.pack(nodeid.Identifier)
    elif nodeid.NodeIdType == ua.NodeIdType.Guid:
        return struct.pack("<BH", nodeid.NodeIdType.value, nodeid.NamespaceIndex) + \
               Primitives.Guid.pack(nodeid.Identifier)
    else:
        return struct.pack("<BH", nodeid.NodeIdType.value, nodeid.NamespaceIndex) + \
            nodeid.Identifier.to_binary()
    # FIXME: Missing NNamespaceURI and ServerIndex


def nodeid_from_binary(data):
    nid = ua.NodeId()
    encoding = ord(data.read(1))
    nid.NodeIdType = ua.NodeIdType(encoding & 0b00111111)

    if nid.NodeIdType == ua.NodeIdType.TwoByte:
        nid.Identifier = ord(data.read(1))
    elif nid.NodeIdType == ua.NodeIdType.FourByte:
        nid.NamespaceIndex, nid.Identifier = struct.unpack("<BH", data.read(3))
    elif nid.NodeIdType == ua.NodeIdType.Numeric:
        nid.NamespaceIndex, nid.Identifier = struct.unpack("<HI", data.read(6))
    elif nid.NodeIdType == ua.NodeIdType.String:
        nid.NamespaceIndex = Primitives.UInt16.unpack(data)
        nid.Identifier = Primitives.String.unpack(data)
    elif nid.NodeIdType == ua.NodeIdType.ByteString:
        nid.NamespaceIndex = Primitives.UInt16.unpack(data)
        nid.Identifier = Primitives.Bytes.unpack(data)
    elif nid.NodeIdType == ua.NodeIdType.Guid:
        nid.NamespaceIndex = Primitives.UInt16.unpack(data)
        nid.Identifier = Primitives.Guid.unpack(data)
    else:
        raise UaError("Unknown NodeId encoding: " + str(nid.NodeIdType))

    if test_bit(encoding, 7):
        nid.NamespaceUri = Primitives.String.unpack(data)
    if test_bit(encoding, 6):
        nid.ServerIndex = Primitives.UInt32.unpack(data)

    return nid


def variant_to_binary(var):
    b = []
    encoding = var.VariantType.value & 0b111111
    if var.is_array or isinstance(var.Value, (list, tuple)):
        var.is_array = True
        encoding = set_bit(encoding, 7)
        if var.Dimensions is not None:
            encoding = set_bit(encoding, 6)
        b.append(Primitives.UInt8.pack(encoding))
        b.append(pack_uatype_array(var.VariantType, ua.flatten(var.Value)))
        if var.Dimensions is not None:
            b.append(pack_uatype_array(ua.VariantType.Int32, var.Dimensions))
    else:
        b.append(Primitives.UInt8.pack(encoding))
        b.append(pack_uatype(var.VariantType, var.Value))

    return b"".join(b)


def variant_from_binary(data):
    dimensions = None
    array = False
    encoding = ord(data.read(1))
    int_type = encoding & 0b00111111
    vtype = ua.datatype_to_varianttype(int_type)
    if test_bit(encoding, 7):
        value = unpack_uatype_array(vtype, data)
        array = True
    else:
        value = unpack_uatype(vtype, data)
    if test_bit(encoding, 6):
        dimensions = unpack_uatype_array(ua.VariantType.Int32, data)
        value = reshape(value, dimensions)
    return ua.Variant(value, vtype, dimensions, is_array=array)


def reshape(flat, dims):
    subdims = dims[1:]
    subsize = 1
    for i in subdims:
        if i == 0:
            i = 1
        subsize *= i
    while dims[0] * subsize > len(flat):
        flat.append([])
    if not subdims or subdims == [0]:
        return flat
    return [reshape(flat[i: i + subsize], subdims) for i in range(0, len(flat), subsize)]


def extensionobject_from_binary(data):
    """
    Convert binary-coded ExtensionObject to a Python object.
    Returns an object, or None if TypeId is zero
    """
    typeid = nodeid_from_binary(data)
    Encoding = ord(data.read(1))
    body = None
    if Encoding & (1 << 0):
        length = Primitives.Int32.unpack(data)
        if length < 1:
            body = Buffer(b"")
        else:
            body = data.copy(length)
            data.skip(length)
    if typeid.Identifier == 0:
        return None
    elif typeid in ua.extension_object_classes:
        klass = ua.extension_object_classes[typeid]
        if body is None:
            raise UaError("parsing ExtensionObject {0} without data".format(klass.__name__))
        return klass.from_binary(body)
    else:
        e = ua.ExtensionObject()
        e.TypeId = typeid
        e.Encoding = Encoding
        if body is not None:
            e.Body = body.read(len(body))
        return e


def extensionobject_to_binary(obj):
    """
    Convert Python object to binary-coded ExtensionObject.
    If obj is None, convert to empty ExtensionObject (TypeId = 0, no Body).
    Returns a binary string
    """
    if isinstance(obj, ua.ExtensionObject):
        return obj.to_binary()
    if obj is None:
        TypeId = ua.NodeId()
        Encoding = 0
        Body = None
    else:
        TypeId = ua.extension_object_ids[obj.__class__.__name__]
        Encoding = 0x01
        Body = to_binary(obj)
    packet = []
    packet.append(to_binary(TypeId))
    packet.append(Primitives.UInt8.pack(Encoding))
    if Body:
        packet.append(Primitives.Bytes.pack(Body))
    return b''.join(packet)


def from_binary(uatype, data):
    print("FROM BIN", uatype, data)
    if isinstance(uatype, (str, unicode)) and uatype.startswith("ListOf"):
        size = Primitives.Int32.unpack(data)
        res = []
        for _ in range(size):
            res.append(from_binary(data, uatype[6:]))
        return res

    elif isinstance(uatype, (str, unicode)) and hasattr(Primitives, uatype):
        st = getattr(Primitives, uatype)
        return st.unpack(data)
    elif uatype == ua.NodeId or uatype == "NodeId":
        return nodeid_from_binary(data)
    elif uatype == ua.Variant or uatype == "Variant":
        return variant_from_binary(data)
    else:
        return struct_from_binary(uatype, data)


def struct_from_binary(objtype, data):
    print("SfB", objtype, data)
    if isinstance(objtype, (unicode, str)):
        obj = getattr(ua, objtype)()
    else:
        obj = objtype()
    print("LOOK", obj.ua_types)
    for name, uatype in obj.ua_types:
        print("AN", name, uatype)
    for name, uatype in obj.ua_types:
        # if our member has a swtich and it is not set we skip it
        if hasattr(obj, "ua_switches") and name in obj.ua_switches:
            container_name, idx = obj.ua_switches[name]
            val = getattr(obj, container_name)
            if not test_bit(val, idx):
                continue
        val = from_binary(uatype, data) 
        setattr(obj, name, val)
    return obj


def header_to_binary(hdr):
    b = []
    b.append(struct.pack("<3ss", hdr.MessageType, hdr.ChunkType))
    size = hdr.body_size + 8
    if hdr.MessageType in (ua.MessageType.SecureOpen, ua.MessageType.SecureClose, ua.MessageType.SecureMessage):
        size += 4
    b.append(Primitives.UInt32.pack(size))
    if hdr.MessageType in (ua.MessageType.SecureOpen, ua.MessageType.SecureClose, ua.MessageType.SecureMessage):
        b.append(Primitives.UInt32.pack(hdr.ChannelId))
    return b"".join(b)


def header_from_string(data):
    hdr = ua.Header()
    hdr.MessageType, hdr.ChunkType, hdr.packet_size = struct.unpack("<3scI", data.read(8))
    hdr.body_size = hdr.packet_size - 8
    if hdr.MessageType in (ua.MessageType.SecureOpen, ua.MessageType.SecureClose, ua.MessageType.SecureMessage):
        hdr.body_size -= 4
        hdr.ChannelId = Primitives.UInt32.unpack(data)
    return hdr

