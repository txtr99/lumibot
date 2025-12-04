inputs:

	Length( 10 ) [DisplayName = "Length", ToolTip = 
	 "Enter the number of bars used in the Gann HiLo calculation."];

;

variables:  
	GHValue( 0 ) ;

GHValue = SQ_GannHiLo(Length ) ;
 
Plot1( GHValue, !("GannHiLo" ));

