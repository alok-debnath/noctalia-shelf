--!nonstrict
-- Shelf panel: browse, reorder, open, copy, and remove what is on the shelf.
-- Dragging files in from other apps (and out to them) happens in the drop
-- window, which this panel opens through the service.

local store = require("./lib/store.luau")

local DRAG_TYPE = "shelf-item"
local FLASH_MS = 2200
local MAX_THUMB_BYTES = 12 * 1000 * 1000

local items = store.current()
local hovered = nil
local flash = nil -- { text = string, untilMs = number }
local helperOpen = noctalia.state.get(store.HELPER_KEY) == true

local render -- forward declaration

local function showFlash(text)
  flash = { text = text, untilMs = noctalia.nowMs() + FLASH_MS }
  noctalia.setUpdateInterval(250)
  render()
end

local function run(argv)
  noctalia.runAsync(argv, function(result)
    if result.exitCode ~= 0 then
      noctalia.log("shelf: " .. argv[1] .. " failed: " .. tostring(result.stderr))
    end
  end)
end

-- Actions ----------------------------------------------------------------------

local function openItem(path)
  if not noctalia.fileExists(path) then
    showFlash(noctalia.tr("panel.flash.missing"))
    return
  end
  run({ "xdg-open", path })
  panel.close()
end

local function revealItem(path)
  -- Ask the file manager to select the file; fall back to opening its folder.
  local uri = store.fileUri(path)
  noctalia.runAsync({
    "gdbus", "call", "--session",
    "--dest", "org.freedesktop.FileManager1",
    "--object-path", "/org/freedesktop/FileManager1",
    "--method", "org.freedesktop.FileManager1.ShowItems",
    "['" .. string.gsub(uri, "'", "%%27") .. "']", "",
  }, function(result)
    if result.exitCode ~= 0 then
      run({ "xdg-open", store.dirname(path) })
    end
  end)
  panel.close()
end

local function copyItems(paths)
  local existing = {}
  for _, p in ipairs(paths) do
    if noctalia.fileExists(p) then
      table.insert(existing, p)
    end
  end
  if #existing == 0 then
    showFlash(noctalia.tr("panel.flash.nothing_to_copy"))
    return
  end
  if noctalia.copyToClipboard(store.uriList(existing), "text/uri-list") then
    showFlash(noctalia.trp("panel.flash.copied", #existing))
  end
end

local function allPaths()
  local paths = {}
  for _, item in ipairs(items) do
    table.insert(paths, item.path)
  end
  return paths
end

local function pasteFromClipboard()
  local paths = store.pathsFromText(noctalia.clipboardText())
  if #paths == 0 then
    showFlash(noctalia.tr("panel.flash.paste_empty"))
    return
  end
  local added = store.add(paths)
  showFlash(noctalia.trp("panel.flash.added", added))
end

local function removeMissing()
  local missing = {}
  for _, item in ipairs(items) do
    if not noctalia.fileExists(item.path) then
      table.insert(missing, item.path)
    end
  end
  store.remove(missing)
end

function onReorder(path, target)
  if target == "end" then
    store.move(path, nil)
  else
    store.move(path, string.sub(target, #"before:" + 1))
  end
end

function onRowHover(state, key)
  local next = (state == "true") and key or nil
  if next ~= hovered then
    hovered = next
    render()
  end
end

-- Views --------------------------------------------------------------------------

local function header(summary, showDropButton)
  return ui.row({ gap = 10, align = "center" }, {
    ui.column({
      width = 36, height = 36, radius = 12, fill = "primary/0.16", align = "center", justify = "center",
    }, {
      ui.glyph({ name = "stack-2", size = 20, color = "primary" }),
    }),
    ui.column({ gap = 1, flexGrow = 1 }, {
      ui.label({ text = noctalia.tr("panel.title"), fontSize = 16, fontWeight = "bold" }),
      ui.label({ text = summary, fontSize = 12, color = "on_surface_variant", maxLines = 1 }),
    }),
    ui.button({
      key = "drop-window",
      visible = showDropButton,
      text = helperOpen and noctalia.tr("panel.drop_window.close") or noctalia.tr("panel.drop_window.open"),
      glyph = "drag-drop",
      variant = helperOpen and "secondary" or "primary",
      controlSize = "sm",
      tooltip = noctalia.tr("panel.drop_window.tooltip"),
      onClick = function()
        store.request(helperOpen and "close" or "open")
      end,
    }),
  })
end

local function emptyState()
  return ui.column({
    flexGrow = 1, gap = 10, align = "center", justify = "center",
    fill = "surface_variant/0.45", radius = 18, padding = 24,
    border = "outline/0.5", borderWidth = 1,
  }, {
    ui.column({
      width = 64, height = 64, radius = 32, fill = "primary/0.14", align = "center", justify = "center",
    }, {
      ui.glyph({ name = "drag-drop", size = 30, color = "primary" }),
    }),
    ui.label({ text = noctalia.tr("panel.empty.title"), fontSize = 15, fontWeight = "bold", textAlign = "center" }),
    ui.label({
      text = noctalia.tr("panel.empty.body"), fontSize = 12, color = "on_surface_variant",
      textAlign = "center", maxWidth = 300,
    }),
    ui.row({ gap = 8, justify = "center" }, {
      ui.button({
        text = noctalia.tr("panel.drop_window.open"), glyph = "drag-drop", variant = "primary",
        controlSize = "sm", onClick = function() store.request("open") end,
      }),
      ui.button({
        text = noctalia.tr("panel.paste.label"), glyph = "clipboard-plus", variant = "outline",
        controlSize = "sm", tooltip = noctalia.tr("panel.paste.tooltip"), onClick = pasteFromClipboard,
      }),
    }),
  })
end

local function thumbnail(path, info)
  if info ~= nil and not info.isDir and store.isImage(path) and info.size <= MAX_THUMB_BYTES then
    return ui.image({ path = path, width = 40, height = 40, radius = 10, fit = "cover" })
  end
  local missing = info == nil
  return ui.column({
    width = 40, height = 40, radius = 10, align = "center", justify = "center",
    fill = missing and "error/0.14" or "surface_variant",
  }, {
    ui.glyph({
      name = missing and "file-off" or store.glyphFor(path, info.isDir),
      size = 20,
      color = missing and "error" or "primary",
    }),
  })
end

local function metaText(path, info)
  if info == nil then
    return noctalia.tr("panel.row.missing") .. " · " .. store.prettyDir(path)
  end
  if info.isDir then
    return noctalia.tr("panel.row.folder") .. " · " .. store.prettyDir(path)
  end
  return store.formatSize(info.size) .. " · " .. store.prettyDir(path)
end

local function insertionZone(value)
  return ui.dropZone({
    key = "gap-" .. value,
    accepts = { DRAG_TYPE },
    value = value,
    onDrop = "onReorder",
    height = 2,
    expandOnDrag = true,
    hitSlop = 22,
  })
end

local function row(item, info)
  local path = item.path
  local isHovered = hovered == path
  local missing = info == nil
  local actions = {}

  if not missing then
    table.insert(actions, ui.button({
      key = "reveal-" .. path, glyph = "folder-open", variant = "ghost", controlSize = "sm",
      tooltip = noctalia.tr("panel.row.reveal"), onClick = function() revealItem(path) end,
    }))
    table.insert(actions, ui.button({
      key = "copy-" .. path, glyph = "copy", variant = "ghost", controlSize = "sm",
      tooltip = noctalia.tr("panel.row.copy"), onClick = function() copyItems({ path }) end,
    }))
  end
  table.insert(actions, ui.button({
    key = "remove-" .. path, glyph = "x", variant = "ghost", controlSize = "sm",
    tooltip = noctalia.tr("panel.row.remove"), onClick = function() store.remove({ path }) end,
  }))

  return ui.row({
    key = path,
    gap = 10,
    align = "center",
    paddingH = 6,
    paddingV = 6,
    radius = 12,
    fill = isHovered and "on_surface/0.07" or "#00000000",
    onHover = "onRowHover",
    onClick = function() openItem(path) end,
  }, {
    ui.dragSource({
      key = "grip-" .. path,
      dragType = DRAG_TYPE,
      payload = path,
      previewAncestor = 1,
      liftFromLayout = true,
      width = 14,
      height = 40,
      tooltip = noctalia.tr("panel.row.reorder"),
    }, {
      ui.glyph({ name = "grip-vertical", size = 14, color = isHovered and "on_surface_variant" or "outline" }),
    }),
    thumbnail(path, info),
    ui.column({ gap = 2, flexGrow = 1 }, {
      ui.label({
        text = store.basename(path), fontSize = 13, fontWeight = "semibold", maxLines = 1,
        color = missing and "error" or "on_surface",
      }),
      ui.label({ text = metaText(path, info), fontSize = 11, color = "on_surface_variant", maxLines = 1 }),
    }),
    ui.row({ gap = 2, align = "center", opacity = isHovered and 1 or 0.55 }, actions),
  })
end

local function footer(missingCount)
  local left = {
    ui.button({
      text = noctalia.tr("panel.copy_all.label"), glyph = "copy", variant = "ghost", controlSize = "sm",
      tooltip = noctalia.tr("panel.copy_all.tooltip"), onClick = function() copyItems(allPaths()) end,
    }),
    ui.button({
      text = noctalia.tr("panel.paste.label"), glyph = "clipboard-plus", variant = "ghost", controlSize = "sm",
      tooltip = noctalia.tr("panel.paste.tooltip"), onClick = pasteFromClipboard,
    }),
  }
  if missingCount > 0 then
    table.insert(left, ui.button({
      text = noctalia.trp("panel.remove_missing", missingCount), glyph = "file-off", variant = "ghost",
      controlSize = "sm", onClick = removeMissing,
    }))
  end
  table.insert(left, ui.spacer({ flexGrow = 1 }))
  table.insert(left, ui.button({
    text = noctalia.tr("panel.clear.label"), glyph = "trash", variant = "ghost", controlSize = "sm",
    tooltip = noctalia.tr("panel.clear.tooltip"), onClick = function() store.clear() end,
  }))
  return ui.row({ gap = 4, align = "center" }, left)
end

render = function()
  local infos = {}
  local totalBytes, missingCount = 0, 0
  for i, item in ipairs(items) do
    local info = noctalia.fileInfo(item.path)
    infos[i] = info
    if info == nil then
      missingCount = missingCount + 1
    elseif not info.isDir then
      totalBytes = totalBytes + info.size
    end
  end

  local summary
  if #items == 0 then
    summary = noctalia.tr("panel.summary.empty")
  elseif totalBytes > 0 then
    summary = noctalia.trp("common.files", #items) .. " · " .. store.formatSize(totalBytes)
  else
    summary = noctalia.trp("common.files", #items)
  end

  -- The empty state has its own call to action, so the header button would repeat it.
  local children = { header(summary, #items > 0 or helperOpen) }

  if #items == 0 then
    table.insert(children, emptyState())
  else
    local rows = {}
    for i, item in ipairs(items) do
      table.insert(rows, insertionZone("before:" .. item.path))
      table.insert(rows, row(item, infos[i]))
    end
    table.insert(rows, insertionZone("end"))
    table.insert(children, ui.scroll({ flexGrow = 1, gap = 0 }, rows))
    table.insert(children, ui.separator({ color = "outline/0.4" }))
    table.insert(children, footer(missingCount))
  end

  if flash ~= nil then
    table.insert(children, ui.row({
      gap = 8, align = "center", justify = "center", paddingV = 6, radius = 999, fill = "primary/0.16",
    }, {
      ui.glyph({ name = "check", size = 14, color = "primary" }),
      ui.label({ text = flash.text, fontSize = 12, color = "primary", fontWeight = "semibold" }),
    }))
  end

  panel.render(ui.column({ gap = 12, flexGrow = 1 }, children))
end

noctalia.state.watch(store.ITEMS_KEY, function(value)
  if type(value) == "table" then
    items = value
    render()
  end
end)

noctalia.state.watch(store.HELPER_KEY, function(value)
  helperOpen = value == true
  render()
end)

function onOpen()
  items = store.current()
  helperOpen = noctalia.state.get(store.HELPER_KEY) == true
  hovered = nil
  render()
end

function update()
  if flash ~= nil and noctalia.nowMs() >= flash.untilMs then
    flash = nil
    noctalia.setUpdateInterval(60000)
    render()
  end
end
