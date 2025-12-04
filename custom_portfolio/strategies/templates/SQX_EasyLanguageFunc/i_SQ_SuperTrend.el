inputs:
	STMode( 1 ) [DisplayName = "Mode", ToolTip = 
	 "Enter the SuperTrend Mode used in the SuperTrend calculation."],
	ATPeriod( 24 ) [DisplayName = "Mode", ToolTip = 
	 "Enter the AT Period used in the SuperTrend calculation."],
	ATRMultiplication( 3 ) [DisplayName = "Length", ToolTip = 
	 "Enter the multiplication used in the SuperTrend calculation."];

variables:  
	ST( 0 ) ;

ST = SQ_SuperTrend(STMode,ATPeriod,ATRMultiplication) ;
 
Plot1( ST, !("SuperTrend" ));


