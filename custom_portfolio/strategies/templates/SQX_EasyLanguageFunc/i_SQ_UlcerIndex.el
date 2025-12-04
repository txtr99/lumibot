
inputs:

	 UIMode( 1 ) [DisplayName = "Mode", ToolTip = 
	 "Enter the Mode to be used in the calculation of the Ulcer Index."],
	 UIPeriod( 14 )[DisplayName = "Period", ToolTip = 
	 "Enter the number of bars to be used in the calculation of the Ulcer Index."];

variables:
	Ulcer( 0 ) ;

Ulcer = SQ_UlcerIndex( UIMode, UIPeriod ) ;
 
Plot1( Ulcer, !( "Ulcer Index" ) ) ;
