# Put small random errors into a (flat) layout: move one edge of N random boxes, or
# shift N random polygons, per layer, by a few tens of nanometres. A clean layout only
# shows that a deck does not cry wolf; this shows whether it catches what it should --
# run your deck and a reference deck on the result and compare them with
# tools/compare_reports.py.
#
#   klayout -b -r tools/perturb_layout.rb -rd in=all.gds -rd out=edited.gds
#           [-rd seed=1] [-rd n=150] [-rd layers=65/20,66/20,...] [-rd steps=10,30,60,120]
# layers: which to edit (default: all); steps: displacement sizes in nm (either sign).
ly = RBA::Layout.new
ly.read($in)
top = ly.top_cell
srand(($seed || "1").to_i)
n = ($n || "150").to_i
steps = ($steps || "10,30,60,120").split(",").map { |s| (s.to_f / 1000.0 / ly.dbu).round }.flat_map { |s| [s, -s] }
want = $layers && $layers.split(",").map { |s| s.split("/").map(&:to_i) }
edits = 0
ly.layer_indexes.each do |li|
  info = ly.get_info(li)
  next if want && !want.include?([info.layer, info.datatype])
  shapes = []
  top.shapes(li).each { |s| shapes << s if s.is_box? || s.is_polygon? || s.is_path? }
  shapes.sample([n, shapes.size].min).each do |s|
    d = steps.sample
    if s.is_box?
      b = s.box
      nb = case rand(4)
           when 0 then RBA::Box.new(b.left + d, b.bottom, b.right, b.top)
           when 1 then RBA::Box.new(b.left, b.bottom, b.right + d, b.top)
           when 2 then RBA::Box.new(b.left, b.bottom + d, b.right, b.top)
           else RBA::Box.new(b.left, b.bottom, b.right, b.top + d)
           end
      next if nb.width <= 0 || nb.height <= 0
      s.box = nb
    else
      s.transform(rand(2) == 0 ? RBA::Trans.new(d, 0) : RBA::Trans.new(0, d))
    end
    edits += 1
  end
end
ly.write($out)
puts "#{edits} edits (seed #{$seed || 1}) -> #{$out}"
