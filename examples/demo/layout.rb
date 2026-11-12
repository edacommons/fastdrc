# A small layout for the demo process, with one planted error per rule (and clean
# counterparts), to try the generated deck on:
#
#   klayout -b -r examples/demo/layout.rb -rd out=demo.gds
#   fastdrc check demo.gds examples/demo
#
# Each case sits in its own 60 um tile along x; the comment says what the deck must report.
ly = RBA::Layout.new; ly.dbu = 0.001; top = ly.create_cell("TOP")
L = { nwell: 1, diff: 2, pimp: 3, poly: 4, cont: 5, met1: 6, via1: 7, met2: 8, thick: 9, chip: 10 }
$top, $ly, $x = top, ly, 0.0
def box(layer, x0, y0, x1, y1)
  $top.shapes($ly.layer(L[layer], 0)).insert(RBA::Box.new(*[x0 + $x, y0, x1 + $x, y1].map { |v| (v * 1000).round }))
end
def tile(n) $x = n * 60.0 end
def tap(x, y)                                  # an n+ tap with a contact and a metal-1 pad
  box(:diff, x, y, x + 1, y + 1); box(:cont, x + 0.4, y + 0.4, x + 0.6, y + 0.6); box(:met1, x + 0.2, y + 0.2, x + 0.8, y + 0.8)
end
def pdiff(x0, y0, x1, y1) box(:diff, x0, y0, x1, y1); box(:pimp, x0 - 0.2, y0 - 0.2, x1 + 0.2, y1 + 0.2) end
def gate(metal_len)                            # a transistor whose gate is tied to a metal-1 line
  box(:diff, 0, 0, 1, 1); box(:poly, 0.4, -0.3, 0.6, 1.3); box(:poly, 0.2, 1.3, 0.8, 1.9)
  box(:cont, 0.4, 1.5, 0.6, 1.7); box(:met1, 0.3, 1.4, 1.8, 1.4 + metal_len)
end

tile(0);  box(:nwell, 0, 0, 0.7, 5)                                                    # NW.1
tile(1);  box(:nwell, 0, 0, 5, 5); box(:nwell, 5.9, 0, 11, 5)                          # NW.2 (and NW.3: untied wells)
tile(2);  box(:nwell, 0, 0, 5, 5); box(:nwell, 6.5, 0, 11.5, 5)                        # NW.3: 1.5 apart, different nets
tile(3);  box(:nwell, 0, 0, 5, 5); box(:nwell, 6.5, 0, 11.5, 5)                        # clean: 1.5 apart, tied together
          tap(2, 2); tap(8.5, 2); box(:met1, 2.3, 2.3, 9.2, 2.7)
tile(4);  box(:nwell, 0, 0, 8, 6); pdiff(6.75, 2, 7.75, 4); box(:diff, 2, 2, 3, 3)     # NW.4_thin: 0.25 inside the well edge
tile(5);  box(:nwell, 0, 0, 8, 6); pdiff(6.5, 2, 7.5, 4); box(:diff, 2, 2, 3, 3)       # NW.4_thick: 0.5 is enough only on thin oxide
          box(:thick, -1, -1, 9, 7)
tile(6);  box(:nwell, 0, 0, 40, 6); pdiff(35, 2, 37, 4); box(:diff, 2, 2, 3, 3)        # LU.1: the tap is 32 um away
tile(7);  box(:poly, 0, 0, 0.1, 5)                                                     # PO.1
tile(8);  box(:diff, 0, 0, 2, 1); box(:poly, 0.9, -0.15, 1.1, 1.15)                    # PO.2: end cap 0.15
tile(9);  box(:diff, 0, 0, 1.3, 1); box(:poly, 1.0, -0.3, 1.2, 1.3)                    # PO.3: diffusion reaches 0.1 past the gate
tile(10); box(:diff, 0, 0, 3, 1); box(:cont, 0.4, 0.4, 0.6, 0.6); box(:cont, 1.4, 0.4, 1.62, 0.62)   # CO.1: one contact 0.22
tile(11); box(:diff, 0, 0, 3, 1); box(:cont, 0.4, 0.4, 0.6, 0.6); box(:cont, 0.8, 0.4, 1.0, 0.6)     # CO.2: 0.2 apart
array = lambda do |x0, n, gx, gy|
  box(:diff, x0 - 0.3, -0.3, x0 + n * (0.2 + gx) + 0.3, n * (0.2 + gy) + 0.3)
  n.times { |i| n.times { |j| box(:cont, x0 + i * (0.2 + gx), j * (0.2 + gy), x0 + i * (0.2 + gx) + 0.2, j * (0.2 + gy) + 0.2) } }
end
tile(12); array.call(0, 4, 0.27, 0.27)                                                 # CO.3: 4 x 4, 0.27 both ways
tile(13); array.call(0, 4, 0.27, 0.35); array.call(10, 3, 0.27, 0.27)                  # clean: one direction is enough; 3 x 3 is no array
tile(14); box(:diff, 0, 0, 2, 1); box(:cont, 0.05, 0.4, 0.25, 0.6)                     # CO.4: enclosed by 0.05
tile(15); box(:met1, 0, 0, 1, 1); box(:via1, 0.05, 0.05, 0.25, 0.25)                   # V1.1: 0.05 on two adjacent sides
          box(:met1, 10, 0, 11, 1); box(:via1, 10.05, 0.2, 10.25, 0.4)                 # clean: 0.05 left, 0.2 below
tile(16); box(:met1, 0, 0, 3, 3); box(:via1, 1, 1, 1.2, 1.2)                           # V1.2: wide metal with one via
          box(:met1, 10, 0, 13, 3); box(:via1, 11, 1, 11.2, 1.2); box(:via1, 11.6, 1, 11.8, 1.2)   # clean: two
tile(17); gate(60)                                                                     # AN.1: 90 um2 of metal on 0.2 um2 of gate
tile(18); gate(40)                                                                     # clean: 60 um2
# metal-1 fill: lines 1.5 wide every 3 um (50 %), and one 100 x 100 corner filled to 95 %
$x = 0.0
box(:chip, -10, -10, 1190, 200)
(0...33).each { |k| box(:met1, 100, 100 + 3 * k, 1180, 101.5 + 3 * k) }
(0...50).each { |k| box(:met1, -10, 100 + 2 * k, 90, 101.9 + 2 * k) }                  # DN.2: this window is 95 % metal
ly.write($out)
