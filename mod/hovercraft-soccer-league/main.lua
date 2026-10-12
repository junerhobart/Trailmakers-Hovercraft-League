HSL = {}

tm.os.DoFile("hsl_core")
tm.os.DoFile("hsl_match")
tm.os.DoFile("hsl_interface")

function update()
    HSL.update()
end

HSL.register()

print("Hovercraft Soccer League loaded. Run /hsl help.")
