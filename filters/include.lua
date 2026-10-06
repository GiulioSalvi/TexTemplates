-- TexTemplates
-- Copyright (C) 2026 Giulio Salvi
--
-- This is free software: you can redistribute it and/or modify
-- it under the terms of the GNU General Public License as published by
-- the Free Software Foundation, either version 3 of the License, or
-- (at your option) any later version.
--
-- This software is distributed in the hope that it will be useful,
-- but WITHOUT ANY WARRANTY; without even the implied warranty of
-- MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
-- GNU General Public License for more details.
--
-- You should have received a copy of the GNU General Public License
-- along with this program. If not, see <https://www.gnu.org/licenses/>.
--
-- SPDX-FileCopyrightText: 2026 Giulio Salvi
-- SPDX-License-Identifier: GPL-3.0-or-later
--
-- A paragraph containing only !include FILE directives is replaced by
-- parsed Markdown blocks. Relative includes and resources belong to the
-- file that declares them. Included YAML metadata is deliberately ignored.

local path = pandoc.path
local system = pandoc.system
local MAX_DEPTH = 64

local function fail(message)
  error('include.lua: ' .. message, 0)
end

local function trim(text)
  return text:match('^%s*(.-)%s*$')
end

local function is_uri(target)
  return target:match('^[%a][%w+%.%-]*:') ~= nil
    or target:match('^//') ~= nil
end

local function absolute(filename, base)
  if path.is_relative(filename) then
    filename = path.join {base, filename}
  end
  filename = path.normalize(filename)
  -- Older Pandoc versions abort outside Lua's pcall if changing directory
  -- fails. Probe the full target first so missing or invalid parent paths
  -- reach include_file's diagnostic with the filename and include chain.
  local probe = io.open(filename, 'rb')
  if not probe then return filename end
  probe:close()
  -- Resolving the containing directory also removes ../ and directory
  -- symlink aliases without invoking a shell or an external interpreter.
  local ok, directory = pcall(system.with_working_directory,
    path.directory(filename), system.get_working_directory)
  if ok then
    return path.join {directory, path.filename(filename)}
  end
  return filename
end

local function source_context(source, chain)
  return ' in ' .. source .. ' (include chain: ' .. table.concat(chain, ' -> ') .. ')'
end

local function attribute_filter(handler)
  local filter = {}
  for _, element in ipairs {
    'Header', 'Div', 'Span', 'CodeBlock', 'Code', 'Link', 'Image', 'Table', 'Figure'
  } do
    filter[element] = handler
  end
  return filter
end

-- pandoc.utils.stringify drops raw HTML and normalizes quoted strings.
-- Keep those literal forms, and refuse to guess which punctuation was
-- removed when Markdown parsed a filename as emphasis or other markup.
local function inline_text(inlines)
  local pieces = {}
  for _, inline in ipairs(inlines) do
    if inline.t == 'Str' then
      pieces[#pieces + 1] = inline.text
    elseif inline.t == 'Space' then
      pieces[#pieces + 1] = ' '
    elseif inline.t == 'SoftBreak' or inline.t == 'LineBreak' then
      pieces[#pieces + 1] = '\n'
    elseif inline.t == 'Quoted' then
      local content, reason = inline_text(inline.content)
      if not content then return nil, reason end
      local quote = inline.quotetype == 'SingleQuote' and "'" or '"'
      pieces[#pieces + 1] = quote .. content .. quote
    elseif inline.t == 'RawInline' and inline.format == 'html' then
      pieces[#pieces + 1] = inline.text
    elseif inline.t == 'Link' and inline.classes:includes('uri') then
      pieces[#pieces + 1] = '<' .. inline.target .. '>'
    elseif inline.t == 'Code' then
      return nil, 'code'
    else
      return nil, 'markup'
    end
  end
  return table.concat(pieces)
end

local function directives(block, source, chain)
  local has_code = false
  block:walk {Code = function() has_code = true end}
  if has_code then return nil end
  local text, reason = inline_text(block.content)
  if not text then
    -- A paragraph containing prose stays literal even if it begins with
    -- an include-looking line. Keep line boundaries for this check.
    local rough_lines = {}
    for _, inline in ipairs(block.content) do
      rough_lines[#rough_lines + 1] =
        (inline.t == 'SoftBreak' or inline.t == 'LineBreak') and '\n'
        or pandoc.utils.stringify(inline)
    end
    for line in (table.concat(rough_lines) .. '\n'):gmatch('(.-)\n') do
      line = trim(line)
      if line ~= '' and line ~= '!include' and not line:match('^!include%s') then
        return nil
      end
    end
    if reason == 'markup' then
      fail('filename contains Markdown formatting; escape its punctuation'
        .. ' with backslashes' .. source_context(source, chain))
    end
    return nil
  end
  local filenames = {}
  for line in (text .. '\n'):gmatch('(.-)\n') do
    line = trim(line)
    if line ~= '' then
      if line ~= '!include' and not line:match('^!include%s') then
        return nil
      end
      filenames[#filenames + 1] = trim(line:sub(9))
    end
  end
  if #filenames == 0 then return nil end
  for index, filename in ipairs(filenames) do
    if filename == '' then
      fail('missing filename after !include' .. source_context(source, chain))
    end
    local first, last = filename:sub(1, 1), filename:sub(-1)
    if first == '"' or first == "'" or first == '<' then
      local closing = first == '<' and '>' or first
      if last ~= closing then
        fail('unclosed filename delimiter after !include'
          .. source_context(source, chain))
      end
      filename = filename:sub(2, -2)
    elseif last == '"' or last == "'" or last == '>' then
      fail('unexpected filename delimiter after !include'
        .. source_context(source, chain))
    end
    if filename == '' or filename:find('%z') then
      fail('invalid empty filename after !include' .. source_context(source, chain))
    end
    if is_uri(filename) then
      fail('only local Markdown files can be included: ' .. filename
        .. source_context(source, chain))
    end
    filenames[index] = filename
  end
  return filenames
end

local function rebase_resource(element, directory)
  local target = element.target or element.src
  if target ~= '' and target:sub(1, 1) ~= '#' and target:sub(1, 1) ~= '?'
      and not is_uri(target) and path.is_relative(target) then
    -- Keep URI fragments and query strings outside filesystem handling.
    local filename, suffix = target:match('^([^?#]*)(.*)$')
    if filename ~= '' then
      local resolved = absolute(filename, directory) .. suffix
      if element.t == 'Image' then element.src = resolved
      else element.target = resolved end
      return element
    end
  end
end

local function root_source()
  local cwd = system.get_working_directory()
  local files = PANDOC_STATE.input_files
  if #files == 0 or (#files == 1 and files[1] == '-') then
    return cwd, '<stdin>', {}, false
  end
  local first = files[1]
  if first == '-' or is_uri(first) then
    return cwd, first, {}, #files > 1
  end
  first = absolute(first, cwd)
  local directory = path.directory(first)
  local ambiguous = false
  for index = 2, #files do
    local filename = files[index]
    if filename == '-' or is_uri(filename)
        or path.directory(absolute(filename, cwd)) ~= directory then
      ambiguous = true
    end
  end
  return directory, first, {first}, ambiguous
end

function Pandoc(document)
  if PANDOC_VERSION[1] < 3
      or (PANDOC_VERSION[1] == 3 and (PANDOC_VERSION[2] or 0) < 1) then
    fail('Pandoc 3.1 or later is required')
  end
  local directory, source, chain, ambiguous_roots = root_source()
  local active = {}
  for _, filename in ipairs(chain) do active[filename] = true end
  local reader_options = pandoc.ReaderOptions(PANDOC_READER_OPTIONS)
  local reader_format = {
    format = 'markdown', extensions = reader_options.extensions
  }
  -- Each file is parsed independently, so automatic heading identifiers
  -- can collide. Reserve the main document's identifiers first. Included
  -- headings receive deterministic suffixes and their same-file fragment
  -- links are rewritten before expanding nested includes.
  local identifiers = {}
  document.blocks:walk(attribute_filter(function(element)
    if element.identifier ~= '' then identifiers[element.identifier] = true end
  end))

  local function unique_headings(blocks, filename)
    local original_ids, replacements = {}, {}
    blocks = blocks:walk(attribute_filter(function(element)
      local original = element.identifier
      if original == '' then return nil end
      if original_ids[original] then
        fail('duplicate identifier #' .. original .. ' in ' .. filename)
      end
      original_ids[original] = true
      local candidate, suffix = original, 1
      while identifiers[candidate] do
        candidate = original .. '-' .. suffix
        suffix = suffix + 1
      end
      identifiers[candidate] = true
      if candidate ~= original then
        replacements[original] = candidate
        element.identifier = candidate
        return element
      end
    end))
    return blocks:walk {Link = function(link)
      if link.target:sub(1, 1) == '#' then
        local fragment = link.target:sub(2):gsub('%%(%x%x)', function(byte)
          return string.char(tonumber(byte, 16))
        end)
        local replacement = replacements[fragment]
        if replacement then
          link.target = '#' .. replacement
          return link
        end
      end
    end}
  end
  local expand_blocks

  local function include_file(filename, base, parent)
    local resolved = absolute(filename, base)
    if active[resolved] then
      fail('include cycle: ' .. table.concat(chain, ' -> ') .. ' -> ' .. resolved)
    end
    if #chain >= MAX_DEPTH then
      fail('maximum include depth (' .. MAX_DEPTH .. ') exceeded'
        .. source_context(parent, chain))
    end
    local handle, open_error = io.open(resolved, 'rb')
    if not handle then
      fail('cannot read included file ' .. resolved .. ': ' .. tostring(open_error)
        .. source_context(parent, chain))
    end
    local contents, read_error = handle:read('*a')
    handle:close()
    if not contents then
      fail('cannot read included file ' .. resolved .. ': ' .. tostring(read_error)
        .. source_context(parent, chain))
    end
    active[resolved] = true
    chain[#chain + 1] = resolved
    local ok, included = pcall(pandoc.read, contents, reader_format, reader_options)
    if not ok then
      fail('cannot parse included Markdown file ' .. resolved .. ': '
        .. tostring(included) .. source_context(parent, chain))
    end
    local scoped = unique_headings(included.blocks, resolved)
    local blocks = expand_blocks(scoped, path.directory(resolved), resolved, true)
    chain[#chain] = nil
    active[resolved] = nil
    return blocks
  end

  expand_blocks = function(blocks, base, current, rebase)
    local function expand_paragraph(block)
      local filenames = directives(block, current, chain)
      if not filenames then return nil end
      if ambiguous_roots then
        fail('includes require one master input file, or input files in the same'
          .. ' directory; source paths are ambiguous' .. source_context(current, chain))
      end
      local replacement = pandoc.Blocks {}
      for _, filename in ipairs(filenames) do
        replacement:extend(include_file(filename, base, current))
      end
      return replacement, false
    end
    local filter = {
      traverse = 'topdown', Para = expand_paragraph, Plain = expand_paragraph
    }
    if rebase then
      filter.Image = function(element) return rebase_resource(element, base) end
      filter.Link = function(element) return rebase_resource(element, base) end
    end
    -- Walking blocks, rather than the document, leaves root metadata intact.
    return blocks:walk(filter)
  end

  document.blocks = expand_blocks(document.blocks, directory, source, false)
  return document
end
