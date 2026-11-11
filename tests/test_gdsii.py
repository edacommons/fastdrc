"""Unit tests for the GDSII flattener (pure Python; no rawast needed).

Build value trees by hand in the shape rawast produces, and check hierarchy
flattening (SREF/AREF + transforms), box, and path stroking.
"""

from fastdrc import gdsii


def _square(x0, y0, x1, y1):
    return [x0, y0, x1, y0, x1, y1, x0, y1, x0, y0]


def test_sref_translates_and_rotates():
    tree = {
        "structures": [
            {"name": "SUB", "elements": [
                {"type": "boundary", "layer": 1, "datatype": 0,
                 "xy": _square(0, 0, 10, 4)}]},
            {"name": "TOP", "elements": [
                # placed at (100,200), no rotation
                {"type": "sref", "name": "SUB", "strans": 0,
                 "xy": [100, 200]},
                # rotated 90° CCW, placed at (0,0): (x,y)->(-y,x)
                {"type": "sref", "name": "SUB", "strans": 0, "angle": 90.0,
                 "xy": [0, 0]}],
             }],
    }
    flat = gdsii.flatten(tree, top="TOP")
    assert len(flat) == 2
    polys = {tuple(sorted(p)) for (_l, _d, p) in flat}
    # instance 1: square (0,0)-(10,4) shifted by (100,200)
    inst1 = [(100, 200), (110, 200), (110, 204), (100, 204)]
    # instance 2: rotate 90° CCW -> corners (0,0),(0,10),(-4,10),(-4,0)
    inst2 = [(0, 0), (0, 10), (-4, 10), (-4, 0)]
    assert tuple(sorted(inst1)) in polys
    assert tuple(sorted(inst2)) in polys


def test_aref_expands_grid():
    tree = {
        "structures": [
            {"name": "CELL", "elements": [
                {"type": "boundary", "layer": 2, "datatype": 0,
                 "xy": _square(0, 0, 5, 5)}]},
            {"name": "TOP", "elements": [
                # 3 cols x 2 rows; col pitch 100 in x, row pitch 50 in y
                {"type": "aref", "name": "CELL", "strans": 0, "colrow": [3, 2],
                 "xy": [0, 0, 300, 0, 0, 100]}]},
        ],
    }
    flat = gdsii.flatten(tree, top="TOP")
    assert len(flat) == 6  # 3 * 2 instances
    origins = {min(p) for (_l, _d, p) in flat}  # lower-left corner of each
    assert origins == {(0, 0), (100, 0), (200, 0),
                       (0, 50), (100, 50), (200, 50)}


def test_box_becomes_polygon():
    tree = {"structures": [{"name": "T", "elements": [
        {"type": "box", "layer": 3, "datatype": 0, "xy": _square(0, 0, 8, 8)}]}]}
    flat = gdsii.flatten(tree)
    assert len(flat) == 1
    layer, dt, pts = flat[0]
    assert (layer, dt) == (3, 0)
    assert tuple(sorted(pts)) == ((0, 0), (0, 8), (8, 0), (8, 8))


def test_manhattan_path_strokes_to_rectangle():
    # horizontal centerline (0,0)-(100,0), width 10, flush ends -> 0..100 x -5..5
    tree = {"structures": [{"name": "T", "elements": [
        {"type": "path", "layer": 4, "datatype": 0, "width": 10,
         "xy": [0, 0, 100, 0]}]}]}
    flat = gdsii.flatten(tree)
    assert len(flat) == 1
    _l, _d, pts = flat[0]
    xs = sorted({x for x, _y in pts})
    ys = sorted({y for _x, y in pts})
    assert xs == [0, 100]
    assert ys == [-5, 5]


def test_undefined_and_cyclic_refs_warn_not_crash():
    warns = []
    tree = {"structures": [
        {"name": "A", "elements": [
            {"type": "sref", "name": "MISSING", "strans": 0, "xy": [0, 0]},
            {"type": "sref", "name": "A", "strans": 0, "xy": [0, 0]}]},  # cycle
    ]}
    flat = gdsii.flatten(tree, top="A", on_warn=warns.append)
    assert flat == []
    assert any("undefined" in w for w in warns)
    assert any("cyclic" in w for w in warns)



def _covers(xy, width, pt, pathtype=0):
    """Is `pt` inside the stroked path (the union of its segment polygons)?"""
    from fastdrc import topology
    tree = {"structures": [{"name": "TOP", "elements": [
        {"type": "path", "layer": 1, "datatype": 0, "width": width,
         "pathtype": pathtype, "xy": xy}]}]}
    return any(topology.contains([(float(x), float(y)) for x, y in pts], pt)
               for _l, _dt, pts in gdsii.flatten(tree, top="TOP"))


def test_path_bend_has_no_corner_notch():
    # An L-shaped flush path, width 10: (0,0)->(100,0)->(100,100). Its outline
    # includes the 5x5 OUTER corner square [100,105]x[-5,0] at the bend, which
    # neither arm's plain rectangle reaches.
    L = [0, 0, 100, 0, 100, 100]
    assert _covers(L, 10, (103, -3))            # inside the outer corner square
    assert _covers(L, 10, (97, 3))              # the inner corner
    assert not _covers(L, 10, (106, -3))        # no overshoot past the miter
    assert not _covers(L, 10, (103, -6))
    assert not _covers(L, 10, (-2, 0))          # flush ends stay flush
    assert not _covers(L, 10, (100, 102))
    # the opposite turn fills the opposite corner
    R = [0, 0, 100, 0, 100, -100]
    assert _covers(R, 10, (103, 3)) and not _covers(R, 10, (106, 3))
    # a 45-degree bend is mitered, not squared off: (0,0)->(100,0)->(200,100).
    # The outer miter point is (100 + 5*tan(22.5deg), -5) ~ (102.07, -5).
    D = [0, 0, 100, 0, 200, 100]
    assert _covers(D, 10, (101, -4.5))
    assert not _covers(D, 10, (104, -4.9))
    # a repeated point does not break the stroke
    assert _covers([0, 0, 100, 0, 100, 0, 100, 100], 10, (103, -3))
    # square ends still extend only the true ends
    assert _covers(L, 10, (-3, 0), pathtype=2) and _covers(L, 10, (100, 103), pathtype=2)
