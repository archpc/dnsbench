using terms from application "System Events"
	tell application "System Events"
		tell process "Icon Composer"
			set w to window "DNSBench.icon"
			set uiEls to entire contents of w
			set ol to missing value
			repeat with e in uiEls
				set r to "ERR"
				try
					set r to role of e as text
				end try
				if r is "AXOutline" then
					set ol to e
					exit repeat
				end if
			end repeat
			set n to count of rows of ol
			set out to "rows=" & n & linefeed
			repeat with i from 1 to n
				set rw to row i of ol
				set lbl to "?"
				try
					set lbl to value of text field 1 of group 1 of cell 1 of rw
				on error
					try
						set lbl to value of static text 1 of group 1 of cell 1 of rw
					end try
				end try
				set out to out & i & ": " & lbl & linefeed
			end repeat
			return out
		end tell
	end tell
end using terms from
