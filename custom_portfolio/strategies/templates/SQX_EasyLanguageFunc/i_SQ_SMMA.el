inputs:
	Price( Close ) [DisplayName = "Price", ToolTip = "Input Price"],
	Period( 12 ) [DisplayName = "Period", ToolTip = "Period"];
;

variables:  
	SMMA( 0 ) ;

SMMA = SQ_SMMA( Price, Period ) ;
 
Plot1( SMMA, !("SMMA" ));
