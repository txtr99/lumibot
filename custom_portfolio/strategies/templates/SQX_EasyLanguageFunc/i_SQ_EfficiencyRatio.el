inputs:
	
	Length( 10 ) [DisplayName = "Length", ToolTip = 
	 "Enter the number of bars used in the KER calculation."];

variables:  
	KER( 0 ) ;

KER = SQ_EfficiencyRatio(Length ) ;
 
Plot1( KER, !("KER" ));

