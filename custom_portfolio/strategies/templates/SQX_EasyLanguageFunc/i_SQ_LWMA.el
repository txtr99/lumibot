inputs:
	Price( Close ) [DisplayName = "Price"],
	Length( 14 ) [DisplayName = "Length"];
;

variables:  
	LWMA( 0 ) ;

LWMA = SQ_LinearWAverage( Price, Length ) ;
 
Plot1( LWMA, !("LWMA" ));
