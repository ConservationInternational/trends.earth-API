"""Bounded PMTiles v3/MVT inspection, run as a credential-stripped subprocess.

This is format validation, not a sandbox or proof of parser safety. Only
uncompressed/gzip archives with one declared MVT source layer are supported.
"""

import io
from itertools import pairwise
import json
import math
from pathlib import Path
import re
import struct
import sys
import zlib

MAX_METADATA_BYTES = 1024**2
MAX_DIRECTORY_BYTES = 4 * 1024**2
MAX_TILE_BYTES = 2 * 1024**2
MAX_ENTRIES = 100_000
MAX_EXPANDED_BYTES = 512 * 1024**2
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,119}$")


def _unpack(data, compression, limit):
    if compression == 1:
        result = data
    elif compression == 2:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        result = decoder.decompress(data, limit + 1)
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("Invalid or oversized gzip data")
    else:
        raise ValueError("Only uncompressed and gzip PMTiles are supported")
    if len(result) > limit:
        raise ValueError("PMTiles decompressed section exceeds its limit")
    return result


def _varint(stream):
    value = 0
    for index in range(10):
        byte = stream.read(1)
        if not byte or (index == 9 and byte[0] > 1):
            raise ValueError("Invalid PMTiles/MVT varint")
        value |= (byte[0] & 127) << (index * 7)
        if byte[0] < 128:
            return value
    raise ValueError("Invalid PMTiles/MVT varint")


def _protobuf(data):
    stream = io.BytesIO(data)
    while stream.tell() < len(data):
        tag = _varint(stream)
        field, wire = tag >> 3, tag & 7
        if not 1 <= field < 2**29:
            raise ValueError("Invalid MVT field")
        if wire == 0:
            value = _varint(stream)
        else:
            if wire == 2:
                length = _varint(stream)
            elif wire in (1, 5):
                length = 8 if wire == 1 else 4
            else:
                raise ValueError("Unsupported MVT wire type")
            if length > len(data) - stream.tell():
                raise ValueError("Truncated MVT field")
            value = stream.read(length)
        yield field, wire, value


def _packed(data):
    stream = io.BytesIO(data)
    while stream.tell() < len(data):
        yield _varint(stream)


def _geometry(data, geometry_type):
    stream = io.BytesIO(data)
    vertices = 0
    closed = False
    while stream.tell() < len(data):
        command = _varint(stream)
        operation, count = command & 7, command >> 3
        if not count or operation not in (1, 2, 7):
            raise ValueError("Invalid MVT geometry command")
        if operation == 7:
            if geometry_type != 3 or count != 1 or vertices < 3 or closed:
                raise ValueError("Invalid MVT polygon close command")
            closed = True
        else:
            if operation == 1:
                if geometry_type != 1 and count != 1:
                    raise ValueError("Invalid MVT path move command")
                if geometry_type == 2 and vertices and vertices < 2:
                    raise ValueError("MVT line has too few vertices")
                if geometry_type == 3 and vertices and not closed:
                    raise ValueError("Unclosed MVT polygon")
                vertices = 0
                closed = False
            elif geometry_type == 1 or not vertices or closed:
                raise ValueError("Invalid MVT line command")
            if count > (len(data) - stream.tell()) // 2:
                raise ValueError("Truncated MVT geometry")
            for _ in range(count * 2):
                if _varint(stream) > 0xFFFFFFFF:
                    raise ValueError("MVT coordinate exceeds uint32")
            vertices += count
    if not vertices:
        raise ValueError("Empty MVT geometry")
    if geometry_type == 2 and vertices < 2:
        raise ValueError("MVT line has too few vertices")
    if geometry_type == 3 and not closed:
        raise ValueError("Unclosed MVT polygon")


def _validate_mvt(data, source_layer, fields):
    layer_count = 0
    feature_count = 0
    for field, wire, layer in _protobuf(data):
        if field != 3 or wire != 2:
            raise ValueError("Unexpected MVT tile field")
        layer_count += 1
        if layer_count > 1:
            raise ValueError("PMTiles must contain only the declared source layer")
        keys, values, features = [], [], []
        name, version, extent = None, None, 4096
        seen = set()
        for number, encoding, value in _protobuf(layer):
            expected = {1: 2, 2: 2, 3: 2, 4: 2, 5: 0, 15: 0}
            if expected.get(number) != encoding:
                raise ValueError("Unexpected MVT layer field")
            if number in (1, 5, 15):
                if number in seen:
                    raise ValueError("Duplicate MVT layer field")
                seen.add(number)
            if number == 1:
                name = value.decode("utf-8")
            elif number == 15:
                version = value
            elif number == 5:
                extent = value
            elif number == 3:
                key = value.decode("utf-8")
                if key not in fields:
                    raise ValueError("MVT attributes must be declared in metadata")
                keys.append(key)
            elif number == 4:
                scalar = list(_protobuf(value))
                if len(scalar) != 1:
                    raise ValueError("Invalid MVT attribute value")
                tag, kind, item = scalar[0]
                if {1: 2, 2: 5, 3: 1, 4: 0, 5: 0, 6: 0, 7: 0}.get(tag) != kind:
                    raise ValueError("Invalid MVT attribute type")
                if tag == 1:
                    item.decode("utf-8")
                if tag in (2, 3) and not math.isfinite(
                    struct.unpack("<f" if tag == 2 else "<d", item)[0]
                ):
                    raise ValueError("Nonfinite MVT attribute")
                if tag == 7 and item not in (0, 1):
                    raise ValueError("Invalid MVT boolean")
                values.append(item)
            elif number == 2:
                features.append(value)
        if name != source_layer or version != 2 or not 1 <= extent <= 65536:
            raise ValueError("MVT source layer, version, or extent is invalid")
        for feature in features:
            tags, geometry, geometry_type = b"", None, None
            seen = set()
            for number, encoding, value in _protobuf(feature):
                if {1: 0, 2: 2, 3: 0, 4: 2}.get(number) != encoding or number in seen:
                    raise ValueError("Invalid MVT feature field")
                seen.add(number)
                if number == 2:
                    tags = value
                elif number == 3:
                    geometry_type = value
                elif number == 4:
                    geometry = value
            indices = list(_packed(tags))
            if len(indices) % 2 or any(
                key >= len(keys) or value >= len(values)
                for key, value in zip(indices[::2], indices[1::2], strict=True)
            ):
                raise ValueError("Invalid MVT attribute index")
            if geometry_type not in (1, 2, 3) or geometry is None:
                raise ValueError("MVT feature has no supported geometry")
            _geometry(geometry, geometry_type)
        feature_count += len(features)
    if layer_count != 1:
        raise ValueError("MVT tile has no declared layer")
    return feature_count


def _directory(data):
    stream = io.BytesIO(data)
    count = _varint(stream)
    if not 1 <= count <= MAX_ENTRIES:
        raise ValueError("PMTiles directory entry limit exceeded")
    ids, runs, lengths, offsets = [], [], [], []
    current_id = 0
    for index in range(count):
        delta = _varint(stream)
        if index and delta == 0:
            raise ValueError("PMTiles tile IDs must increase")
        current_id += delta
        ids.append(current_id)
    for _ in range(count):
        runs.append(_varint(stream))
    for _ in range(count):
        length = _varint(stream)
        if not length:
            raise ValueError("PMTiles directory entry has zero length")
        lengths.append(length)
    for index in range(count):
        offset = _varint(stream)
        if offset == 0:
            if not index:
                raise ValueError("Invalid initial PMTiles offset")
            offsets.append(offsets[index - 1] + lengths[index - 1])
        else:
            offsets.append(offset - 1)
    if stream.tell() != len(data):
        raise ValueError("Trailing PMTiles directory bytes")
    return list(zip(ids, runs, lengths, offsets, strict=True))


def validate_pmtiles(path, source_layer, maximum_zoom=22):
    """Check every directory and unique tile under fixed expansion/work limits."""
    if not NAME_RE.fullmatch(source_layer):
        raise ValueError("Invalid declared source layer")
    size = Path(path).stat().st_size
    with Path(path).open("rb") as archive:
        header = archive.read(127)
        if len(header) != 127 or header[:8] != b"PMTiles\x03":
            raise ValueError("Only PMTiles v3 archives are supported")
        numbers = struct.unpack_from("<11Q", header, 8)
        sections = list(zip(numbers[:8:2], numbers[1:8:2], strict=True))
        occupied = sorted(
            (start, start + length) for start, length in sections if length
        )
        if any(start < 127 or end > size for start, end in occupied) or any(
            end > next_start for (_, end), (next_start, _) in pairwise(occupied)
        ):
            raise ValueError("PMTiles sections overlap or extend outside the file")
        root, metadata_section, leaves, tiles = sections
        if not root[1] or root[0] + root[1] > 16384 or not tiles[1]:
            raise ValueError("Invalid PMTiles root directory or empty tile data")
        clustered, internal, compression, tile_type, minzoom, maxzoom = header[96:102]
        if (
            clustered not in (0, 1)
            or internal not in (1, 2)
            or compression not in (1, 2)
            or tile_type != 1
            or not 0 <= minzoom <= maxzoom <= maximum_zoom
            or not minzoom <= header[118] <= maxzoom
        ):
            raise ValueError("Unsupported PMTiles type, compression, or zooms")
        bounds = [
            value / 10_000_000 for value in struct.unpack_from("<4i", header, 102)
        ]
        if not (
            -180 <= bounds[0] <= bounds[2] <= 180
            and -85.051129 <= bounds[1] <= bounds[3] <= 85.051129
        ):
            raise ValueError("Invalid PMTiles geographic bounds")

        def read_section(start, length, encoding, limit):
            if length > limit:
                raise ValueError("PMTiles section exceeds its byte limit")
            archive.seek(start)
            return _unpack(archive.read(length), encoding, limit)

        metadata_bytes = read_section(*metadata_section, internal, MAX_METADATA_BYTES)
        metadata = json.loads(metadata_bytes)
        if not isinstance(metadata, dict):
            raise ValueError("PMTiles metadata must be an object")  # noqa: TRY004
        layers = metadata.get("vector_layers")
        if not isinstance(layers, list) or len(layers) != 1:
            raise ValueError("PMTiles must declare exactly one vector layer")
        layer = layers[0]
        if not isinstance(layer, dict) or layer.get("id") != source_layer:
            raise ValueError("PMTiles source layer does not match the declaration")
        fields = layer.get("fields")
        if (
            not isinstance(fields, dict)
            or len(fields) > 256
            or any(
                not NAME_RE.fullmatch(key)
                or not isinstance(value, str)
                or value.lower() not in ("string", "number", "boolean")
                for key, value in fields.items()
            )
        ):
            raise ValueError("Invalid PMTiles attribute metadata")
        pending = [(root[0], root[1], 0, (4**maxzoom * 4 - 1) // 3)]
        visited, contents = set(), set()
        expanded = len(metadata_bytes)
        entries = addressed = features = tile_entries = 0
        last_end = (4**minzoom - 1) // 3
        while pending:
            start, length, lower, upper = pending.pop()
            if (start, length) in visited:
                raise ValueError("Repeated or cyclic PMTiles directory")
            visited.add((start, length))
            data = read_section(start, length, internal, MAX_DIRECTORY_BYTES)
            expanded += len(data)
            directory = _directory(data)
            if any(item[1] == 0 for item in directory) and any(
                item[1] != 0 for item in directory
            ):
                raise ValueError(
                    "Mixed PMTiles leaf and tile directories are unsupported"
                )
            entries += len(directory)
            if entries > MAX_ENTRIES or expanded > MAX_EXPANDED_BYTES:
                raise ValueError("PMTiles work/expansion limit exceeded")
            children = []
            for index, (tile_id, run, tile_length, offset) in enumerate(directory):
                next_id = (
                    directory[index + 1][0] if index + 1 < len(directory) else upper
                )
                if not lower <= tile_id < next_id <= upper:
                    raise ValueError("PMTiles directory tile range is invalid")
                if run == 0:
                    if offset + tile_length > leaves[1]:
                        raise ValueError("PMTiles leaf pointer is outside its section")
                    # Accept only a root plus one level of leaves (no recursion).
                    if start != root[0]:
                        raise ValueError(
                            "Nested PMTiles leaf directories are unsupported"
                        )
                    children.append((leaves[0] + offset, tile_length, tile_id, next_id))
                else:
                    tile_entries += 1
                    if tile_id < last_end or tile_id + run > next_id:
                        raise ValueError(
                            "PMTiles tile runs overlap or exceed zoom bounds"
                        )
                    last_end = tile_id + run
                    if offset + tile_length > tiles[1]:
                        raise ValueError("PMTiles tile pointer is outside its section")
                    addressed += run
                    contents.add((offset, tile_length))
            pending.extend(reversed(children))
        if not contents or len(contents) > MAX_ENTRIES:
            raise ValueError("PMTiles has no tiles or too many tile contents")
        for offset, length in contents:
            data = read_section(tiles[0] + offset, length, compression, MAX_TILE_BYTES)
            expanded += len(data)
            if expanded > MAX_EXPANDED_BYTES:
                raise ValueError("PMTiles total decompression limit exceeded")
            features += _validate_mvt(data, source_layer, fields)
        if not features:
            raise ValueError("PMTiles contains no features")
        if numbers[8] and numbers[8] != addressed:
            raise ValueError("PMTiles addressed tile count does not match")
        if numbers[9] and numbers[9] != tile_entries:
            raise ValueError("PMTiles tile entry count does not match")
        if numbers[10] and numbers[10] != len(contents):
            raise ValueError("PMTiles tile content count does not match")
    return {
        "source_layer": source_layer,
        "minzoom": minzoom,
        "maxzoom": maxzoom,
        "fields": {key: value.lower() for key, value in fields.items()},
        "bounds": bounds,
    }


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024**2, 16 * 1024**2))
    try:
        result = validate_pmtiles(sys.argv[1], sys.argv[2], int(sys.argv[3]))
    except (ValueError, OSError, zlib.error, RecursionError) as exc:
        # Return only a bounded diagnosis, never uploaded metadata or file paths.
        sys.stdout.write(json.dumps({"error": type(exc).__name__}) + "\n")
        sys.exit(1)
    sys.stdout.write(json.dumps(result) + "\n")
