-- TeX Templates - Copyright (C) 2026 Giulio Salvi
-- GNU GPL v3 or later; without any warranty. See LICENSE.
-- SPDX-License-Identifier: GPL-3.0-or-later

local environments = {
  'propertybox', 'definitionbox', 'theorembox', 'axiombox', 'lemmabox',
  'observationbox', 'tipbox', 'sidebarsection', 'proofsection', 'timeanalysissection'
}
local commands = {'TransparentBox', 'beginbox', 'currentboxlabel', 'stepboxcounter'}

function Pandoc(document)
  if not FORMAT:match('latex') and FORMAT ~= 'beamer' then return document end
  local needed = document.meta.boxes == true
  local function inspect(element)
    if element.format ~= 'tex' and element.format ~= 'latex' then return end
    for _, name in ipairs(environments) do
      if element.text:match('\\begin%s*{%s*' .. name .. '%s*}') then needed = true end
    end
    for _, name in ipairs(commands) do
      if element.text:match('\\' .. name .. '%f[^%a@]') then needed = true end
    end
  end
  document.blocks:walk {RawBlock = inspect, RawInline = inspect}
  if not needed then return document end
  local lines = {'\\usepackage{tt-boxes}'}
  for key, command in pairs {
    ['box-image-diameter'] = 'BoxImageDiameter',
    ['box-padding'] = 'BoxPadding',
    ['box-title-padding'] = 'BoxTitlePadding'
  } do
    if document.meta[key] then
      local value = pandoc.utils.stringify(document.meta[key])
      if not value:match('^%d+%.?%d*%a+$') then
        error('components.lua: ' .. key .. ' must be a LaTeX dimension such as 8mm')
      end
      lines[#lines+1] = '\\renewcommand{\\' .. command .. '}{' .. value .. '}'
    end
  end
  local headers = pandoc.List {pandoc.MetaBlocks {pandoc.RawBlock('latex', table.concat(lines, '\n'))}}
  local old = document.meta['header-includes']
  if old then
    if pandoc.utils.type(old) == 'List' then headers:extend(old) else headers:insert(old) end
  end
  document.meta['header-includes'] = headers
  return document
end
