#include <memory>


namespace exo_simu
{
    template<class F,class dF=std::decay_t<F>>// dF optional
    auto not_f(F&& f){
        return [f=std::forward<F>(f)](auto&&...args) mutable
               ->decltype(!std::declval<std::result_of_t<dF&(decltype(args)...)>>()) // optional, adds sfinae
               {
                   return !f(decltype(args)(args)...);
               };
    }

	template<typename KeyType, typename ValueType>
	std::vector<ValueType> getMapValues(std::map<KeyType, ValueType> m)
	{
		std::vector<ValueType> v;
        v.reserve(m.size());
		std::transform(m.begin(),
                       m.end(),
                       std::back_inserter(v), 
                       [](std::pair<KeyType const, ValueType> & pair) -> ValueType
                       {
                           return pair.second;
                       });
		return v;
	}

	template<typename typeOut, typename typeIn>
	std::vector<std::shared_ptr<typeOut> > staticCastSharedPtrVector(std::vector<std::shared_ptr<typeIn> > vIn)
    {
		std::vector<std::shared_ptr<typeOut> > vOut;
        vOut.reserve(vIn.size());
		std::transform(vIn.begin(),
                       vIn.end(),
                       std::back_inserter(vIn), 
                       [](std::shared_ptr<typeIn> & e) -> std::shared_ptr<typeOut>
                       {
                           return std::static_pointer_cast<typeOut>(e);
                       });
		return vOut;
    }
}