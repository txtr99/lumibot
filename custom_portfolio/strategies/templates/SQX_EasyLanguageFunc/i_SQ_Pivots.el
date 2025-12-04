inputs:
	StartHour( 0 ) [DisplayName = "StartHour", ToolTip = ""],
	StartMinute( 0 ) [DisplayName = "StartMinute", ToolTip = ""],
	Line( 0 ) [DisplayName = "Line", ToolTip = "0=PP,1=R1,2=R2,3=R3,4=S1,5=S2,6=S3"];

variables:  
	PivotsValue( 0 ) ;

PivotsValue = SQ_Pivots( StartHour, StartMinute, Line ) ;
 
Plot1( PivotsValue, !("SQ Pivots" ));
