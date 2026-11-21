--[[
Whitespace at the edges of inline markup (deviations Q22).

CommonMark drops one space at each end of a code span that starts and ends
with a space ("` top `" shows as "top"), and does not read "*S' *" as
emphasis (a closing "*" may not follow a space). Pandoc writes both, so the
text a viewer shows loses spaces or gains asterisks. This filter moves such
edge whitespace outside the markup; the visible text stays the same.

Runs after anchors.lua (specconv passes it second).
]]

local NBSP = "\194\160"

-- Pandoc's inline whitespace elements; a line break at an edge moves out too.
local function is_space(el)
  return el.t == "Space" or el.t == "SoftBreak" or el.t == "LineBreak"
end

-- Emph, Strong, Strikeout, Underline: move edge Space and edge non-breaking
-- spaces of Str elements outside the element. (Lua patterns cannot repeat a
-- multi-byte character, so the UTF-8 non-breaking space is matched in a loop.)
local function trim_markup(el)
  local content = el.content
  local before, after = pandoc.Inlines({}), pandoc.Inlines({})
  while #content > 0 and is_space(content[1]) do
    before:insert(content:remove(1))
  end
  while #content > 0 and is_space(content[#content]) do
    after:insert(1, content:remove(#content))
  end
  if #content > 0 and content[1].t == "Str" then
    local text, lead = content[1].text, ""
    while text:sub(1, #NBSP) == NBSP do
      lead, text = lead .. NBSP, text:sub(#NBSP + 1)
    end
    if lead ~= "" then
      before:insert(pandoc.Str(lead))
      content[1].text = text
    end
  end
  if #content > 0 and content[#content].t == "Str" then
    local text, tail = content[#content].text, ""
    while #text >= #NBSP and text:sub(-#NBSP) == NBSP do
      tail, text = NBSP .. tail, text:sub(1, -#NBSP - 1)
    end
    if tail ~= "" then
      after:insert(1, pandoc.Str(tail))
      content[#content].text = text
    end
  end
  if #before == 0 and #after == 0 then
    return nil -- nil tells Pandoc to keep the element unchanged
  end
  local out = pandoc.Inlines({})
  out:extend(before)
  -- Markup that held only whitespace disappears; the whitespace stays.
  if #content > 0 then
    el.content = content
    out:insert(el)
  end
  out:extend(after)
  return out
end

-- Code: a span that starts and ends with a space loses one at each end in
-- CommonMark; write those two spaces outside the code span instead.
local function Code(code)
  local text = code.text
  -- Only when there is non-space text: CommonMark keeps all-space code spans as is.
  if #text > 2 and text:sub(1, 1) == " " and text:sub(-1) == " " and text:match("%S") then
    code.text = text:sub(2, -2)
    return { pandoc.Space(), code, pandoc.Space() }
  end
  return nil
end

return {
  {
    Emph = trim_markup,
    Strong = trim_markup,
    Strikeout = trim_markup,
    Underline = trim_markup,
    Code = Code,
  },
}
