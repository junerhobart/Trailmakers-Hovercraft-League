HSL.TEAM_RED = 0
HSL.TEAM_BLUE = 1
HSL.TEAM_SPECTATOR = 2
HSL.KICKOFF_SECONDS = 3
HSL.BALL_RESET_SECONDS = 3
HSL.PENALTY_SECONDS = 30
HSL.SHOT_MOVEMENT_DISTANCE_SQUARED = 4
HSL.HOLD_DISTANCE_SQUARED = 2.25
HSL.SPAWN_LOCATION = "hsl_dynamic"

local TEAM_RED = HSL.TEAM_RED
local TEAM_BLUE = HSL.TEAM_BLUE
local TEAM_SPECTATOR = HSL.TEAM_SPECTATOR

HSL.setup = {
    kickoff = {
        [TEAM_RED] = {},
        [TEAM_BLUE] = {}
    },
    penalty = {},
    away = nil
}

HSL.match = {
    phase = "idle",
    deadline = nil,
    shootingTeam = nil,
    shooterId = nil,
    shooterStart = nil,
    held = {},
    repairLocks = {},
    cooldowns = {},
    profileCooldowns = {},
    validityCheckAt = 0,
    official = false
}

function HSL.copyVector(value)
    return { x = value.x, y = value.y, z = value.z }
end

local function vector(value)
    return tm.vector3.Create(value.x, value.y, value.z)
end

function HSL.distanceSquared(first, second)
    local x = first.x - second.x
    local y = first.y - second.y
    local z = first.z - second.z
    return x * x + y * y + z * z
end

function HSL.offset(position, right, forward, rightAmount, forwardAmount)
    return {
        x = position.x + right.x * rightAmount + forward.x * forwardAmount,
        y = position.y + right.y * rightAmount + forward.y * forwardAmount,
        z = position.z + right.z * rightAmount + forward.z * forwardAmount
    }
end

function HSL.capturePose(playerId)
    local transform = tm.players.GetPlayerTransform(playerId)
    return {
        position = HSL.copyVector(transform.GetPositionWorld()),
        rotation = HSL.copyVector(transform.GetEulerAnglesWorld()),
        forward = HSL.copyVector(transform.Forward()),
        right = HSL.copyVector(transform.Right())
    }
end

function HSL.tell(playerId, message)
    tm.playerUI.SendChatMessageToPlayer(playerId, "HSL", message)
end

function HSL.announce(header, message, duration)
    tm.playerUI.ShowIntrusiveMessageForAllPlayers(header, message, duration or 3)
    tm.playerUI.SendChatMessage("HSL", header .. ": " .. message)
end

function HSL.isAdministrator(playerId)
    return playerId == 0 or tm.players.IsPlayerAdministrator(playerId)
end

function HSL.sideFromName(name)
    if name == nil then return nil end
    name = string.lower(name)
    if name == "red" then return TEAM_RED end
    if name == "blue" then return TEAM_BLUE end
    return nil
end

function HSL.teamFromName(name)
    local teamId = HSL.sideFromName(name)
    if teamId ~= nil then return teamId end
    if name ~= nil and string.lower(name) == "spectator" then return TEAM_SPECTATOR end
    return nil
end

function HSL.sideName(teamId)
    if teamId == TEAM_RED then return "Red" end
    if teamId == TEAM_BLUE then return "Blue" end
    return "Spectator"
end

function HSL.currentPlayers()
    local players = tm.players.CurrentPlayers()
    table.sort(players, function(first, second)
        return first.playerId < second.playerId
    end)
    return players
end

function HSL.teamPlayers(teamId)
    local players = {}
    for _, player in ipairs(HSL.currentPlayers()) do
        if player.teamId == teamId then
            table.insert(players, player)
        end
    end
    return players
end

function HSL.playerIsConnected(playerId)
    for _, player in ipairs(tm.players.CurrentPlayers()) do
        if player.playerId == playerId then return true end
    end
    return false
end

function HSL.teamsAreBalanced()
    local redCount = #HSL.teamPlayers(TEAM_RED)
    local blueCount = #HSL.teamPlayers(TEAM_BLUE)
    return redCount > 0 and redCount == blueCount, redCount, blueCount
end

function HSL.setupIsReady()
    local _, redCount, blueCount = HSL.teamsAreBalanced()
    if redCount == 0 or blueCount == 0 then
        return false, "Red and Blue each need at least one player."
    end
    if #HSL.setup.kickoff[TEAM_RED] < redCount then
        return false, "Red needs " .. redCount .. " saved kickoff slots."
    end
    if #HSL.setup.kickoff[TEAM_BLUE] < blueCount then
        return false, "Blue needs " .. blueCount .. " saved kickoff slots."
    end
    return true, nil
end

function HSL.matchIsActive()
    return HSL.match.phase ~= "idle" and HSL.match.phase ~= "finished"
end

function HSL.markMatchUnofficial(reason)
    if not HSL.matchIsActive() or not HSL.match.official then return end
    HSL.match.official = false
    HSL.announce("Exhibition", reason .. "; result will not count", 4)
end

function HSL.teleportToPose(playerId, pose)
    tm.players.SetSpawnPoint(playerId, HSL.SPAWN_LOCATION, vector(pose.position), vector(pose.rotation))
    tm.players.SetPlayerSpawnLocation(playerId, HSL.SPAWN_LOCATION)
    tm.players.TeleportPlayerToSpawnPoint(playerId, HSL.SPAWN_LOCATION, true)
end

function HSL.lockRepair(playerId, reason)
    local lock = HSL.match.repairLocks[playerId]
    if lock == nil then
        lock = {
            original = tm.players.GetRepairEnabled(playerId),
            reasons = {},
            count = 0
        }
        HSL.match.repairLocks[playerId] = lock
    end
    if lock.reasons[reason] ~= true then
        lock.reasons[reason] = true
        lock.count = lock.count + 1
    end
    tm.players.SetRepairEnabled(playerId, false)
end

function HSL.unlockRepair(playerId, reason)
    local lock = HSL.match.repairLocks[playerId]
    if lock == nil or lock.reasons[reason] ~= true then return end
    lock.reasons[reason] = nil
    lock.count = lock.count - 1
    if lock.count == 0 then
        if HSL.playerIsConnected(playerId) then
            tm.players.SetRepairEnabled(playerId, lock.original)
        end
        HSL.match.repairLocks[playerId] = nil
    end
end

function HSL.clearPhaseHolds()
    for playerId, _ in pairs(HSL.match.held) do
        HSL.unlockRepair(playerId, "phase")
    end
    HSL.match.held = {}
end

function HSL.holdPlayer(playerId, pose)
    HSL.match.held[playerId] = pose
    HSL.lockRepair(playerId, "phase")
end

function HSL.clearCooldowns()
    for playerId, _ in pairs(HSL.match.cooldowns) do
        HSL.unlockRepair(playerId, "cooldown")
    end
    HSL.match.cooldowns = {}
    HSL.match.profileCooldowns = {}
end

function HSL.clearTransientState()
    HSL.clearPhaseHolds()
    HSL.match.deadline = nil
    HSL.match.shootingTeam = nil
    HSL.match.shooterId = nil
    HSL.match.shooterStart = nil
end
