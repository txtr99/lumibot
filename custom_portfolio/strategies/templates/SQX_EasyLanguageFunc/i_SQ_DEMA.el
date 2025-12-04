inputs:
	AppliedPrice( 0 ) [DisplayName = "Price"],
	Length( 12 ) [DisplayName = "Length", ToolTip = 
	 "Enter the number of bars used in the DEMA calculation."];
;

variables:  
	DEMAValue( 0 ) ;

DEMAValue = SQ_DEMA( AppliedPrice, Length ) ;
 
Plot1( DEMAValue,!("DEMA" ));


