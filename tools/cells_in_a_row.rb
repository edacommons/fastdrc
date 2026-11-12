# Put every top cell of one or more layout files side by side in one flat cell -- a
# real layout to try a DRC deck on (e.g. a standard-cell library).
#
#   klayout -b -r tools/cells_in_a_row.rb -rd in=lib.gds[,more.gds | dir] -rd out=all.gds
#           [-rd top=ALLCELLS] [-rd gap=5.0]
files = $in.split(",").flat_map { |f| File.directory?(f) ? Dir.glob(File.join(f, "*.gds")).sort : [f] }
ly = RBA::Layout.new
files.each { |f| ly.read(f) }
tops = ly.top_cells.sort_by(&:name)
top = ly.create_cell($top || "ALLCELLS")
gap = (($gap || "5.0").to_f / ly.dbu).round
x = 0
tops.each do |c|
  b = c.bbox
  next if b.empty?
  top.insert(RBA::CellInstArray.new(c.cell_index, RBA::Trans.new(x - b.left, -b.bottom)))
  x += b.width + gap
end
top.flatten(true)
opt = RBA::SaveLayoutOptions.new
opt.add_cell(top.cell_index)
ly.write($out, opt)
puts "#{tops.size} cells in a row, #{(x * ly.dbu).round(2)} um wide -> #{$out} (top #{top.name})"
