
inputs:
	VWAPPeriod(10) ;

variables:  
	Indy( 0 ) ;

Indy = SQ_VWAP( VWAPPeriod) ;

Plot1( Indy, "VWAP" );

