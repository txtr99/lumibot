inputs:
	type(1),
	startHours( 0 ),
	startMinutes( 0 ),
	endHours( 0 ),
	endMinutes( 0 ),
	daysShift( 0 );

variables:  
	Value( 0 );

Value = SQ_SessionOHLC(type, startHours, startMinutes, endHours, endMinutes, daysShift) ;
 
 Plot1( Value , "Session OHLC");
 { 
Switch(Type) begin
    	Case 1: Plot1( Value , "Session Open (" + startHours + ":" + startMinutes + ")[" + daysShift + "]"); 
    	Case 2: Plot1( Value , Text("Session High (" + startHours + ":" + startMinutes + "-" + endHours+ ":" + endMinutes + ")[" + daysShift + "]"));
    	Case 3: Plot1( Value , Text("Session Low (" + startHours + ":" + startMinutes + "-" + endHours+ ":" + endMinutes + ")[" + daysShift + "]"));    	
    	Case 4: Plot1( Value , Text("Session Close (" + endHours+ ":" + endMinutes + ")[" + daysShift + "]"));
end;
 }
