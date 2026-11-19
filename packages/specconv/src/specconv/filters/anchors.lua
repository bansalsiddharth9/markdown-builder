--[[
anchors.lua: keep in-page link targets that Pandoc's GFM output would drop.

Design: "Heading and element ids", plus decision I3 in spec/deviations.md.

- Heading ids, and the ids of the <section> elements that contain them, are
  written as empty anchors on the heading line itself:
      ## <a id="sec-7"></a><a id="x-sec-7"></a>7 Modes
  A plain <section> wrapper (no classes other than "section", no attributes)
  is then removed; wrappers with other classes (e.g. "informative") are kept.
- Ids on table rows or cells that a link targets: if the table is written as
  raw HTML they stay on the rows and cells; if it is written as a pipe table
  an empty anchor for each is placed just before the table.
- Any other element with an id that a link targets (divs, code blocks,
  figures, tables, inline code, images) gets an empty anchor just before it.
  Ids nobody links to are dropped from those elements.
- Spans and links keep their ids; Pandoc already writes them as
  <span id="…"> and <a id="…">, e.g. the empty anchors of older W3C specs.
- Link targets are never changed.

Metadata "anchor-headings" (set with -M by specconv) chooses which heading
ids are kept: "all" (default; HTML, whose heading ids come from the source
and are linked to from other documents) or "targeted" (DOCX, where Pandoc
invents heading ids that no other document links to; only ids that a link
in the document targets, e.g. from a table of contents, are kept).
]]

local targeted = {}
local emitted = {}
local all_headings = true

local function escape_attr(s)
  return (s:gsub("&", "&amp;"):gsub('"', "&quot;"):gsub("<", "&lt;"):gsub(">", "&gt;"))
end

local function anchor_html(ids)
  local parts = {}
  for _, id in ipairs(ids) do
    if id ~= "" and not emitted[id] then
      emitted[id] = true
      parts[#parts + 1] = '<a id="' .. escape_attr(id) .. '"></a>'
    end
  end
  return table.concat(parts)
end

local function url_decode(s)
  return (s:gsub("%%(%x%x)", function(h) return string.char(tonumber(h, 16)) end))
end

local function is_targeted(id)
  return id ~= nil and id ~= "" and targeted[id] == true
end

-- Prepend anchors for ids to a block list (used for "just before it").
local function with_anchor_before(ids, block)
  local html = anchor_html(ids)
  if html == "" then
    return block
  end
  return { pandoc.RawBlock("html", html), block }
end

---------------------------------------------------------------- headings

local function prepend_to_header(header, ids)
  local html = anchor_html(ids)
  if html ~= "" then
    header.content:insert(1, pandoc.RawInline("html", html))
  end
  return header
end

local function Header(h)
  if h.identifier ~= "" and (all_headings or targeted[h.identifier]) then
    prepend_to_header(h, { h.identifier })
  end
  h.identifier = ""
  return h
end

------------------------------------------------------------------- divs

local function is_plain_section(div)
  if not div.classes:includes("section") or #div.classes ~= 1 then
    return false
  end
  for _ in pairs(div.attributes) do
    return false
  end
  return true
end

local function Div(div)
  local id = div.identifier
  if div.classes:includes("section") then
    -- The section's id goes on the line of its first heading.
    local first = div.content[1]
    if id ~= "" then
      if first ~= nil and first.t == "Header" then
        -- Anchors already added for the heading are kept after the section's.
        local html = anchor_html({ id })
        if html ~= "" then
          first.content:insert(1, pandoc.RawInline("html", html))
        end
        div.identifier = ""
      else
        div.identifier = ""
        if is_plain_section(div) then
          local blocks = pandoc.Blocks({})
          local html = anchor_html({ id })
          if html ~= "" then
            blocks:insert(pandoc.RawBlock("html", html))
          end
          blocks:extend(div.content)
          return blocks
        end
        return with_anchor_before({ id }, div)
      end
    end
    if is_plain_section(div) then
      return div.content
    end
    return div
  end
  if id ~= "" then
    div.identifier = ""
    if is_targeted(id) then
      return with_anchor_before({ id }, div)
    end
  end
  return div
end

---------------------------------------------------- other block elements

-- Pandoc's GFM writer uses an indented block for code without any attribute;
-- write those as a fenced block, with a fence longer than any backtick run inside.
local function fenced(cb)
  if cb.identifier ~= "" or #cb.classes > 0 then
    return cb
  end
  for _ in pairs(cb.attributes) do
    return cb
  end
  local longest = 0
  for run in cb.text:gmatch("`+") do
    longest = math.max(longest, #run)
  end
  local fence = string.rep("`", math.max(3, longest + 1))
  return pandoc.RawBlock("gfm", fence .. "\n" .. cb.text .. "\n" .. fence)
end

local function CodeBlock(cb)
  -- The id is left on the block: GFM drops it anyway, and removing every
  -- attribute would make Pandoc switch from a fenced to an indented block.
  if is_targeted(cb.identifier) then
    return with_anchor_before({ cb.identifier }, cb)
  end
  return fenced(cb)
end

local function Figure(fig)
  local id = fig.identifier
  if is_targeted(id) then
    fig.identifier = ""
    return with_anchor_before({ id }, fig)
  end
  return fig
end

local function each_row(tbl, fn)
  for _, row in ipairs(tbl.head.rows) do fn(row) end
  for _, body in ipairs(tbl.bodies) do
    for _, row in ipairs(body.head) do fn(row) end
    for _, row in ipairs(body.body) do fn(row) end
  end
  for _, row in ipairs(tbl.foot.rows) do fn(row) end
end

local function Table(tbl)
  local ids = {}
  local id = tbl.identifier
  -- GFM has no table attributes: Pandoc would write the id, classes and styles
  -- after the caption ("Caption {#id .common style=...}"), so all are cleared.
  tbl.attr = pandoc.Attr("", {}, {})
  if is_targeted(id) then
    ids[#ids + 1] = id
  end
  local written = pandoc.write(pandoc.Pandoc({ tbl }), "gfm", { wrap_text = "none" })
  local is_pipe = written:match("^%s*|") ~= nil
  if is_pipe then
    -- Pipe tables cannot hold ids: targeted row and cell ids move before the table.
    each_row(tbl, function(row)
      if is_targeted(row.identifier) then
        ids[#ids + 1] = row.identifier
      end
      row.identifier = ""
      for _, cell in ipairs(row.cells) do
        if is_targeted(cell.identifier) then
          ids[#ids + 1] = cell.identifier
        end
        cell.identifier = ""
      end
    end)
  else
    -- Raw HTML keeps row and cell ids; remember them so they are not repeated.
    each_row(tbl, function(row)
      if row.identifier ~= "" then emitted[row.identifier] = true end
      for _, cell in ipairs(row.cells) do
        if cell.identifier ~= "" then emitted[cell.identifier] = true end
      end
    end)
  end
  return with_anchor_before(ids, tbl)
end

--------------------------------------------------------- inline elements

local function inline_with_anchor(el)
  local id = el.identifier
  if id ~= "" then
    el.identifier = ""
    if is_targeted(id) then
      local html = anchor_html({ id })
      if html ~= "" then
        return { pandoc.RawInline("html", html), el }
      end
    end
  end
  return el
end

local function Span(span)
  if span.identifier ~= "" then
    emitted[span.identifier] = true -- written by Pandoc as <span id="…">
  end
  return span
end

local function Link(link)
  if link.identifier ~= "" then
    emitted[link.identifier] = true -- written by Pandoc as <a id="…">
  end
  return link
end

---------------------------------------------------------------- document

function Pandoc(doc)
  local mode = doc.meta["anchor-headings"]
  if mode ~= nil then
    mode = pandoc.utils.stringify(mode)
    if mode ~= "all" and mode ~= "targeted" then
      error("anchor-headings must be 'all' or 'targeted', got '" .. mode .. "'")
    end
    all_headings = mode == "all"
    doc.meta["anchor-headings"] = nil
  end
  doc:walk({
    Link = function(l)
      local t = l.target
      if t:sub(1, 1) == "#" and #t > 1 then
        targeted[t:sub(2)] = true
        targeted[url_decode(t:sub(2))] = true
      end
    end,
  })
  -- Spans and links first, so ids Pandoc writes itself are never repeated.
  doc = doc:walk({ Span = Span, Link = Link })
  -- Bottom-up: a heading gets its own anchor before its section adds one in front.
  doc = doc:walk({ Header = Header, Div = Div })
  doc = doc:walk({
    CodeBlock = CodeBlock,
    Figure = Figure,
    Table = Table,
    Code = inline_with_anchor,
    Image = inline_with_anchor,
  })
  return doc
end
