local TEAM_RED = HSL.TEAM_RED
local TEAM_BLUE = HSL.TEAM_BLUE
local match = HSL.match
local setup = HSL.setup

local function kickoffPoseFor(player, teamList)
    for index, teammate in ipairs(teamList) do
        if teammate.playerId == player.playerId then
            return setup.kickoff[player.teamId][index]
        end
    end
    return nil
end

function HSL.beginKickoff(message)
    local ready, problem = HSL.setupIsReady()
    if not ready then return false, problem end

    HSL.clearTransientState()

    local red = HSL.teamPlayers(TEAM_RED)
    local blue = HSL.teamPlayers(TEAM_BLUE)
    for _, player in ipairs(red) do
        local pose = kickoffPoseFor(player, red)
        HSL.teleportToPose(player.playerId, pose)
        HSL.holdPlayer(player.playerId, pose)
    end
    for _, player in ipairs(blue) do
        local pose = kickoffPoseFor(player, blue)
        HSL.teleportToPose(player.playerId, pose)
        HSL.holdPlayer(player.playerId, pose)
    end

    match.phase = "kickoff"
    match.deadline = tm.os.GetTime() + HSL.KICKOFF_SECONDS
    HSL.announce("Kickoff in 3", message or "Teams set", HSL.KICKOFF_SECONDS)
    return true, nil
end

function HSL.stopMatch(message)
    HSL.clearTransientState()
    HSL.clearCooldowns()
    match.phase = "idle"
    match.official = false
    HSL.announce("Match stopped", message or "", 3)
end

local function spreadPose(basePose, index, count, behindDistance)
    local columns = math.min(count, 4)
    local row = math.floor((index - 1) / 4)
    local column = (index - 1) % 4
    local rowCount = math.min(columns, count - row * 4)
    local lateral = (column - (rowCount - 1) / 2) * 12
    local position = HSL.offset(basePose.position, basePose.right, basePose.forward, lateral, -(behindDistance + row * 12))
    return {
        position = position,
        rotation = basePose.rotation,
        forward = basePose.forward,
        right = basePose.right
    }
end

local function lineUpPenalty()
    local shooters = HSL.teamPlayers(match.shootingTeam)
    if #shooters == 0 then
        HSL.clearPhaseHolds()
        match.phase = "live"
        match.deadline = nil
        match.shootingTeam = nil
        HSL.announce("Penalty cancelled", "No players on shooting team", 3)
        return
    end

    local shooter = shooters[math.random(1, #shooters)]
    local shooterPose = setup.penalty[match.shootingTeam]
    HSL.teleportToPose(shooter.playerId, shooterPose)

    local others = {}
    for _, player in ipairs(HSL.currentPlayers()) do
        if (player.teamId == TEAM_RED or player.teamId == TEAM_BLUE) and player.playerId ~= shooter.playerId then
            table.insert(others, player)
        end
    end
    for index, player in ipairs(others) do
        local pose = spreadPose(shooterPose, index, #others, 18)
        HSL.teleportToPose(player.playerId, pose)
        HSL.holdPlayer(player.playerId, pose)
    end

    match.phase = "penalty"
    match.deadline = tm.os.GetTime() + HSL.PENALTY_SECONDS
    match.shooterId = shooter.playerId
    match.shooterStart = HSL.copyVector(shooterPose.position)
    HSL.announce("Penalty: " .. HSL.sideName(match.shootingTeam), shooter.playerName .. " shoots", 4)
end

function HSL.beginPenalty(teamId)
    local ready, problem = HSL.setupIsReady()
    if not ready then return false, problem end
    if setup.away == nil then return false, "Save an away point with /hsl setaway." end
    if setup.penalty[teamId] == nil then
        return false, "Save the " .. string.lower(HSL.sideName(teamId)) .. " penalty spot first."
    end

    HSL.clearTransientState()

    local matchPlayers = {}
    for _, player in ipairs(HSL.currentPlayers()) do
        if player.teamId == TEAM_RED or player.teamId == TEAM_BLUE then
            table.insert(matchPlayers, player)
        end
    end
    for index, player in ipairs(matchPlayers) do
        local pose = spreadPose(setup.away, index, #matchPlayers, 0)
        HSL.teleportToPose(player.playerId, pose)
        HSL.holdPlayer(player.playerId, pose)
    end

    match.phase = "penalty-reset"
    match.shootingTeam = teamId
    match.deadline = tm.os.GetTime() + HSL.BALL_RESET_SECONDS
    HSL.announce("Penalty setup", "Resetting the native ball", HSL.BALL_RESET_SECONDS)
    return true, nil
end

local function releasePenalty(reason)
    HSL.clearPhaseHolds()
    match.phase = "live"
    match.deadline = nil
    match.shootingTeam = nil
    match.shooterId = nil
    match.shooterStart = nil
    HSL.announce("Play", reason, 2)
end

local function enforceHolds()
    for playerId, pose in pairs(match.held) do
        if HSL.playerIsConnected(playerId) then
            local position = tm.players.GetPlayerTransform(playerId).GetPositionWorld()
            if HSL.distanceSquared(position, pose.position) > HSL.HOLD_DISTANCE_SQUARED then
                HSL.teleportToPose(playerId, pose)
            end
        end
    end
end

local function updateCooldowns(now)
    for playerId, endsAt in pairs(match.cooldowns) do
        if now >= endsAt then
            match.cooldowns[playerId] = nil
            HSL.unlockRepair(playerId, "cooldown")
            if HSL.playerIsConnected(playerId) then
                HSL.tell(playerId, "Respawn wait finished.")
            end
        end
    end
end

local function checkMatchValidity(now)
    if now < match.validityCheckAt or not match.official then return end
    match.validityCheckAt = now + 1
    local balanced = HSL.teamsAreBalanced()
    if HSL.matchIsActive() and not balanced then
        HSL.markMatchUnofficial("Teams became uneven")
    end
end

function HSL.update()
    local now = tm.os.GetTime()
    updateCooldowns(now)
    checkMatchValidity(now)
    enforceHolds()

    if match.phase == "kickoff" and now >= match.deadline then
        HSL.clearPhaseHolds()
        match.phase = "live"
        match.deadline = nil
        HSL.announce("Play", "Native Carball is live", 2)
    elseif match.phase == "penalty-reset" and now >= match.deadline then
        HSL.clearPhaseHolds()
        lineUpPenalty()
    elseif match.phase == "penalty" then
        if not HSL.playerIsConnected(match.shooterId) then
            releasePenalty("Shooter left")
            return
        end
        local shooterPosition = tm.players.GetPlayerTransform(match.shooterId).GetPositionWorld()
        if HSL.distanceSquared(shooterPosition, match.shooterStart) >= HSL.SHOT_MOVEMENT_DISTANCE_SQUARED then
            releasePenalty("Shot started")
        elseif now >= match.deadline then
            releasePenalty("30 seconds expired")
        end
    end
end
