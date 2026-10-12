local TEAM_RED = HSL.TEAM_RED
local TEAM_BLUE = HSL.TEAM_BLUE
local TEAM_SPECTATOR = HSL.TEAM_SPECTATOR
local match = HSL.match
local setup = HSL.setup

local function startCooldown(player)
    if not HSL.matchIsActive() then return end
    local endsAt = tm.os.GetTime() + 10
    match.cooldowns[player.playerId] = endsAt
    match.profileCooldowns[player.playerProfileId] = endsAt
    HSL.lockRepair(player.playerId, "cooldown")
    HSL.tell(player.playerId, "Respawn locked for 10 seconds.")
end

local function onPlayerDied(player, damage)
    startCooldown(player)
end

local function onPlayerLeft(player)
    if not HSL.matchIsActive() then return end
    if player.teamId == TEAM_RED or player.teamId == TEAM_BLUE then
        HSL.markMatchUnofficial("A player left")
    end
    local endsAt = tm.os.GetTime() + 10
    match.profileCooldowns[player.playerProfileId] = endsAt
    match.held[player.playerId] = nil
    match.cooldowns[player.playerId] = nil
    match.repairLocks[player.playerId] = nil
end

local function setOwnTeam(playerId, teamId)
    if tm.players.GetPlayerTeam(playerId) == teamId then
        HSL.tell(playerId, "Already on " .. HSL.sideName(teamId) .. ".")
        return
    end
    if HSL.matchIsActive() then
        HSL.markMatchUnofficial("A player changed teams")
    end
    tm.players.SetPlayerTeam(playerId, teamId)
    HSL.tell(playerId, "Joined " .. HSL.sideName(teamId) .. ".")
end

local function chooseRed(callback)
    setOwnTeam(callback.playerId, TEAM_RED)
end

local function chooseBlue(callback)
    setOwnTeam(callback.playerId, TEAM_BLUE)
end

local function chooseSpectator(callback)
    setOwnTeam(callback.playerId, TEAM_SPECTATOR)
end

local function addTeamPicker(playerId)
    tm.playerUI.AddUILabel(playerId, "hsl_team_label", "Choose a team")
    tm.playerUI.AddUIButton(playerId, "hsl_team_red", "Join Red", chooseRed)
    tm.playerUI.AddUIButton(playerId, "hsl_team_blue", "Join Blue", chooseBlue)
    tm.playerUI.AddUIButton(playerId, "hsl_team_spectator", "Spectate", chooseSpectator)
end

local function onPlayerJoined(player)
    addTeamPicker(player.playerId)
    local endsAt = match.profileCooldowns[player.playerProfileId]
    if endsAt ~= nil and endsAt > tm.os.GetTime() then
        match.cooldowns[player.playerId] = endsAt
        HSL.lockRepair(player.playerId, "cooldown")
        HSL.tell(player.playerId, "Return wait is still active.")
    end
end

local function onPlayerTeamChanged(player, newTeamId)
    if HSL.matchIsActive() then
        HSL.markMatchUnofficial("A player changed teams")
    end
end

local function namesForTeam(teamId)
    local names = {}
    for _, player in ipairs(HSL.teamPlayers(teamId)) do
        table.insert(names, player.playerName)
    end
    return names
end

local function finishMatch(winnerTeamId)
    if not HSL.matchIsActive() then
        return false, "No match is running."
    end

    local balanced = HSL.teamsAreBalanced()
    local isOfficial = match.official and balanced
    local redPlayers = namesForTeam(TEAM_RED)
    local bluePlayers = namesForTeam(TEAM_BLUE)

    HSL.clearTransientState()
    HSL.clearCooldowns()
    match.phase = "finished"
    match.official = false

    if isOfficial then
        local result = {
            version = 1,
            official = true,
            winner = string.lower(HSL.sideName(winnerTeamId)),
            redPlayers = redPlayers,
            bluePlayers = bluePlayers
        }
        tm.os.WriteAllText_Dynamic("match-result.json", json.serialize(result))
        HSL.announce(HSL.sideName(winnerTeamId) .. " wins", "Official result queued", 5)
        return true, nil
    end

    HSL.announce(HSL.sideName(winnerTeamId) .. " wins", "Exhibition result not submitted", 5)
    return false, "Result not submitted because teams were not equal for the match."
end

local function sendHelp(playerId)
    HSL.tell(playerId, "/hsl team red|blue|spectator - choose your team")
    HSL.tell(playerId, "/hsl add red|blue - save a kickoff slot")
    HSL.tell(playerId, "/hsl setpenalty red|blue - save shooter position")
    HSL.tell(playerId, "/hsl setaway - save the ball-reset waiting area")
    HSL.tell(playerId, "/hsl start | reset | stop | status")
    HSL.tell(playerId, "/hsl penalty red|blue - run the penalty sequence")
    HSL.tell(playerId, "/hsl winner red|blue - finish and submit a valid match")
    HSL.tell(playerId, "/hsl clear red|blue|all - clear setup")
end

local function onCommand(sender, args)
    local action = args[1] and string.lower(args[1]) or "help"

    if action == "help" then
        sendHelp(sender.playerId)
        return
    end
    if action == "status" then
        local balanced, redCount, blueCount = HSL.teamsAreBalanced()
        HSL.tell(sender.playerId, "Phase: " .. match.phase .. "; Red " .. redCount .. ", Blue " .. blueCount .. ".")
        if HSL.matchIsActive() then
            HSL.tell(sender.playerId, "Counts as official: " .. tostring(match.official and balanced) .. ".")
        end
        HSL.tell(sender.playerId, "Slots: Red " .. #setup.kickoff[TEAM_RED] .. ", Blue " .. #setup.kickoff[TEAM_BLUE] .. ".")
        return
    end
    if action == "team" then
        local teamId = HSL.teamFromName(args[2])
        if teamId == nil then
            HSL.tell(sender.playerId, "Usage: /hsl team red|blue|spectator")
            return
        end
        setOwnTeam(sender.playerId, teamId)
        return
    end
    if not HSL.isAdministrator(sender.playerId) then
        HSL.tell(sender.playerId, "Only the host or a session administrator can do that.")
        return
    end

    if action == "add" then
        local teamId = HSL.sideFromName(args[2])
        if teamId == nil then
            HSL.tell(sender.playerId, "Usage: /hsl add red|blue")
            return
        end
        table.insert(setup.kickoff[teamId], HSL.capturePose(sender.playerId))
        HSL.tell(sender.playerId, HSL.sideName(teamId) .. " kickoff slot " .. #setup.kickoff[teamId] .. " saved.")
    elseif action == "setpenalty" then
        local teamId = HSL.sideFromName(args[2])
        if teamId == nil then
            HSL.tell(sender.playerId, "Usage: /hsl setpenalty red|blue")
            return
        end
        setup.penalty[teamId] = HSL.capturePose(sender.playerId)
        HSL.tell(sender.playerId, HSL.sideName(teamId) .. " shooter position saved.")
    elseif action == "setaway" then
        setup.away = HSL.capturePose(sender.playerId)
        HSL.tell(sender.playerId, "Ball-reset waiting area saved.")
    elseif action == "start" then
        local balanced = HSL.teamsAreBalanced()
        match.official = balanced
        local started, problem = HSL.beginKickoff("Match started")
        if not started then
            match.official = false
            HSL.tell(sender.playerId, problem)
        elseif not balanced then
            tm.playerUI.SendChatMessage("HSL", "Teams are uneven. This match is an exhibition and will not be submitted.")
        end
    elseif action == "reset" then
        if not HSL.matchIsActive() then
            HSL.tell(sender.playerId, "No match is running. Use /hsl start.")
            return
        end
        local started, problem = HSL.beginKickoff("Kickoff reset")
        if not started then HSL.tell(sender.playerId, problem) end
    elseif action == "penalty" then
        if not HSL.matchIsActive() then
            HSL.tell(sender.playerId, "No match is running.")
            return
        end
        local teamId = HSL.sideFromName(args[2])
        if teamId == nil then
            HSL.tell(sender.playerId, "Usage: /hsl penalty red|blue")
            return
        end
        local started, problem = HSL.beginPenalty(teamId)
        if not started then HSL.tell(sender.playerId, problem) end
    elseif action == "winner" then
        local teamId = HSL.sideFromName(args[2])
        if teamId == nil then
            HSL.tell(sender.playerId, "Usage: /hsl winner red|blue")
            return
        end
        local submitted, problem = finishMatch(teamId)
        if not submitted and problem ~= nil then HSL.tell(sender.playerId, problem) end
    elseif action == "clear" then
        local target = args[2] and string.lower(args[2]) or ""
        if target == "all" then
            setup.kickoff[TEAM_RED] = {}
            setup.kickoff[TEAM_BLUE] = {}
            setup.penalty = {}
            setup.away = nil
            HSL.tell(sender.playerId, "All setup cleared.")
        else
            local teamId = HSL.sideFromName(target)
            if teamId == nil then
                HSL.tell(sender.playerId, "Usage: /hsl clear red|blue|all")
                return
            end
            setup.kickoff[teamId] = {}
            setup.penalty[teamId] = nil
            HSL.tell(sender.playerId, HSL.sideName(teamId) .. " setup cleared.")
        end
    elseif action == "stop" then
        HSL.stopMatch("Controls restored")
    else
        sendHelp(sender.playerId)
    end
end

function HSL.register()
    tm.os.SetModTargetDeltaTime(0.1)
    tm.players.OnPlayerDied.add(onPlayerDied)
    tm.players.OnPlayerLeft.add(onPlayerLeft)
    tm.players.OnPlayerJoined.add(onPlayerJoined)
    tm.players.OnPlayerTeamChanged.add(onPlayerTeamChanged)
    tm.playerUI.RegisterChatCommand("hsl", {}, "/hsl help", onCommand)

    for _, player in ipairs(HSL.currentPlayers()) do
        addTeamPicker(player.playerId)
    end
end
