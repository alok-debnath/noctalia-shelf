--!nonstrict
-- Owns the drop window. Noctalia panels cannot take part in Wayland drag and
-- drop, so the service starts helper/shelf-window.py (GTK4), reads the JSON
-- events it prints, and applies them to the store. Panel and widget ask for
-- the window through the shelf.request state key.

local store = require("./lib/store.luau")

local HELPER = "helper/shelf-window.py"
local PALETTE_ROLES = {
  "primary", "on_primary", "surface", "surface_variant",
  "on_surface", "on_surface_variant", "outline", "error",
}

local lastRequestId = nil
local depsChecked = false
local depsOk = false

noctalia.state.set(store.ITEMS_KEY, store.load())
noctalia.state.set(store.HELPER_KEY, false)

local function shellQuote(value)
  return "'" .. string.gsub(value, "'", "'\\''") .. "'"
end

local function python()
  local configured = noctalia.string.trim(noctalia.getConfig("python") or "")
  if configured ~= "" then
    return noctalia.expandPath(configured)
  end
  -- The GTK bindings ship with the system interpreter; a python3 from
  -- Homebrew, pyenv, or a venv earlier on PATH usually lacks them.
  if noctalia.fileExists("/usr/bin/python3") then
    return "/usr/bin/python3"
  end
  return "python3"
end

local function paletteJson()
  if noctalia.getColor == nil then
    return nil
  end
  local colors = {}
  for _, role in ipairs(PALETTE_ROLES) do
    local ok, value = pcall(noctalia.getColor, role)
    if ok and type(value) == "string" then
      colors[role] = value
    end
  end
  return noctalia.json.encode(colors)
end

local function onHelperLine(line)
  local event = noctalia.json.decode(line)
  if type(event) ~= "table" then
    noctalia.log("shelf window: " .. line)
    return
  end
  if event.op == "add" and type(event.paths) == "table" then
    store.add(event.paths)
  elseif event.op == "remove" and type(event.paths) == "table" then
    store.remove(event.paths)
  elseif event.op == "clear" then
    store.clear()
  elseif event.op == "ready" then
    noctalia.state.set(store.HELPER_KEY, true)
  elseif event.op == "bye" then
    noctalia.state.set(store.HELPER_KEY, false)
  end
end

local function helperCommand(toggle)
  local parts = {
    shellQuote(python()),
    shellQuote(noctalia.pluginDir() .. "/" .. HELPER),
    "--store", shellQuote(store.path()),
    "--position", shellQuote(noctalia.getConfig("window_position") or "cursor"),
  }
  local colors = paletteJson()
  if colors ~= nil then
    table.insert(parts, "--colors")
    table.insert(parts, shellQuote(colors))
  end
  if noctalia.getConfig("remove_after_drag") then
    table.insert(parts, "--remove-after-drag")
  end
  if noctalia.getConfig("close_after_drag") then
    table.insert(parts, "--close-after-drag")
  end
  if toggle then
    table.insert(parts, "--toggle")
  end
  return table.concat(parts, " ")
end

local function launch(toggle)
  if store.path() == nil then
    noctalia.notifyError(noctalia.tr("notify.no_data_dir"))
    return
  end
  -- Every launch gets its own stream. When a window is already up, the new
  -- process hands its arguments to that window over D-Bus and exits, and the
  -- original stream keeps delivering events. When the window died without
  -- saying goodbye, the new process becomes the window itself.
  if not noctalia.runStream(helperCommand(toggle), onHelperLine) then
    noctalia.notifyError(noctalia.tr("notify.launch_failed"))
  end
end

-- Checks once that the interpreter has the GTK 4 bindings, so a missing
-- package produces a clear notification instead of a window that never shows.
local function withDeps(fn)
  if depsChecked then
    if depsOk then
      fn()
    else
      noctalia.notifyError(noctalia.tr("notify.missing_deps.title"), noctalia.tr("notify.missing_deps.body"))
    end
    return
  end
  local check = "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk"
  noctalia.runAsync({ python(), "-c", check }, function(result)
    depsChecked = true
    depsOk = result.exitCode == 0
    if not depsOk then
      noctalia.log("shelf: GTK 4 check failed: " .. tostring(result.stderr))
    end
    withDeps(fn)
  end)
end

local function handle(action)
  if action == "open" then
    withDeps(function() launch(false) end)
  elseif action == "toggle" then
    withDeps(function() launch(noctalia.state.get(store.HELPER_KEY) == true) end)
  elseif action == "close" then
    if noctalia.state.get(store.HELPER_KEY) == true then
      launch(true)
    end
  end
end

noctalia.state.watch(store.REQUEST_KEY, function(request)
  if type(request) ~= "table" or request.id == lastRequestId then
    return
  end
  lastRequestId = request.id
  handle(request.action)
end)

-- A setting changed. Keep the store; the window picks up new settings the
-- next time it opens.
function onConfigChanged()
  depsChecked = false
end

-- noctalia msg plugin alok-debnath/shelf:service all <event> [payload]
function onIpc(event, payload)
  if event == "toggle" or event == "open" or event == "close" then
    handle(event)
  elseif event == "add" then
    local paths = store.pathsFromText(payload)
    if #paths > 0 then
      store.add(paths)
    else
      noctalia.log("shelf: add ignored, no existing path in: " .. tostring(payload))
    end
  elseif event == "clear" then
    store.clear()
  elseif event == "panel" then
    noctalia.togglePanel(store.PANEL_ID)
  else
    noctalia.log("shelf: unknown IPC event " .. tostring(event))
  end
end
