inputs:
	Period( 12 ) [DisplayName = "Period", ToolTip = "Period"];

variables:  
	PSIValue( 0 ) ;

PSIValue = SQ_PsychologyIndex(Period);
 
Plot1( PSIValue, !("PsychologyIndex"));
